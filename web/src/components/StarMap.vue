<script setup lang="ts">
/**
 * StarMap —— 3D 关系星图（渲染内核移植自 RelationView，视觉参数零改动）。
 *
 * 与旧版的区别（只修逻辑）：
 *  1. 数据全部走 props，组件不知道业务；
 *  2. 布局由 useGraphLayout 提供（原力导算法泛化版），数据变更时异步重排；
 *  3. 静态边合并为单个 LineSegments（一次 draw call，构建一次、零帧开销）；
 *  4. 节点标签 LOD：同屏最多 80 个（聚焦/高亮优先，其余按权重）；
 *  5. 边标签只为聚焦节点的邻域边创建；
 *  6. 高亮 = 一次性脉冲环 + 排名徽标；其余节点淡化到 18%（不删除，保留空间记忆）；
 *  7. raycast 节流（60ms）+ 拖拽时跳过。
 */
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import * as THREE from 'three'
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { hash, homeEye, layoutGraph, visualScale } from '@/composables/useGraphLayout'
import type { GraphEdge, GraphNode, NodeKind } from '@/types/graph'
import { EDGE_ROLE_LABEL } from '@/types/graph'

const props = withDefaults(
  defineProps<{
    nodes: GraphNode[]
    edges: GraphEdge[]
    focusId?: string | null
    highlightIds?: string[]
    ranks?: Record<string, number>
    dimOthers?: boolean
  }>(),
  { focusId: null, highlightIds: () => [], ranks: () => ({}), dimOthers: true },
)

const emit = defineEmits<{
  (e: 'select', id: string | null): void
  (e: 'hover', id: string | null): void
  (e: 'ready', ms: number): void
}>()

const host = ref<HTMLDivElement | null>(null)

/* ---- 视觉参数（冻结，与旧版一致） ---- */
const tint: Record<NodeKind, string> = {
  project: '#f0d0b0',
  winner: '#fff6ec',
  bidder: '#efe4d6',
  vendor: '#f0d8bc',
  buyer: '#f4e7d6',
}
const innerOp: Record<NodeKind, number> = { project: 0.86, winner: 0.84, bidder: 0.58, vendor: 0.5, buyer: 0 }
const hazeOp: Record<NodeKind, number> = { project: 0.2, winner: 0.18, bidder: 0.14, vendor: 0.16, buyer: 0.32 }
const hazeMul: Record<NodeKind, number> = { project: 2.05, winner: 2.15, bidder: 2.25, vendor: 2.45, buyer: 1.02 }

const LABEL_CAP = 80
const SEG = 32
/** 静态边的雾分组数：任意时刻只有 1–2 组随呼吸亮起，其余淡出 */
const MIST_GROUPS = 6
const MIST_SAMPLES = 36

type NodeLive = {
  id: string
  kind: NodeKind
  base: THREE.Vector3
  phase: number
  opacity: number
  target: number
  scale: number
  labelOp: number
  sprite: THREE.Sprite
  haze: THREE.Sprite
  pick: THREE.Mesh
  labelObj: CSS2DObject
  rankObj: CSS2DObject | null
}

type Pulse = { sprite: THREE.Sprite; t: number; size: number }

type FocusLine = {
  a: string
  b: string
  role: GraphEdge['role']
  line: THREE.Line
  tagObj: CSS2DObject
}

type MistGroup = {
  points: THREE.Points
  mat: THREE.PointsMaterial
  phase: number
  op: number
}

let cleanup = () => {}

onMounted(() => {
  const el = host.value
  if (!el) return
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  /* ---- 渲染器与场景 ---- */
  const renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setClearColor(0x0c0b0a, 1)
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.width = '100%'
  renderer.domElement.style.height = '100%'
  renderer.domElement.style.touchAction = 'none'
  renderer.domElement.style.cursor = 'grab'
  el.appendChild(renderer.domElement)

  const labels = new CSS2DRenderer()
  labels.domElement.style.position = 'absolute'
  labels.domElement.style.inset = '0'
  labels.domElement.style.pointerEvents = 'none'
  el.appendChild(labels.domElement)

  const world = new THREE.Scene()
  world.background = new THREE.Color('#0c0b0a')
  world.fog = new THREE.FogExp2(0x0c0b0a, 0.007)

  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 2000)
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = !reduce
  controls.dampingFactor = 0.065
  controls.rotateSpeed = 0.4
  controls.zoomSpeed = 0.5

  function makeTex(stops: [number, string][]) {
    const size = 256
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const g = canvas.getContext('2d')!
    const r = size / 2
    const grd = g.createRadialGradient(r, r, 0, r, r, r)
    for (const [at, color] of stops) grd.addColorStop(at, color)
    g.fillStyle = grd
    g.fillRect(0, 0, size, size)
    const tex = new THREE.CanvasTexture(canvas)
    tex.colorSpace = THREE.SRGBColorSpace
    return tex
  }

  const starTex = makeTex([
    [0, 'rgba(255,255,255,0.72)'],
    [0.06, 'rgba(255,255,255,0.22)'],
    [0.16, 'rgba(255,255,255,0.05)'],
    [0.4, 'rgba(255,255,255,0.01)'],
    [1, 'rgba(255,255,255,0)'],
  ])
  const cloudTex = makeTex([
    [0, 'rgba(255,255,255,0.14)'],
    [0.24, 'rgba(255,255,255,0.06)'],
    [0.68, 'rgba(255,255,255,0.015)'],
    [1, 'rgba(255,255,255,0)'],
  ])
  const discTex = makeTex([
    [0, 'rgba(255,255,255,0.42)'],
    [0.28, 'rgba(255,255,255,0.22)'],
    [0.55, 'rgba(255,255,255,0.06)'],
    [1, 'rgba(255,255,255,0)'],
  ])
  const ringTex = makeTex([
    [0, 'rgba(255,255,255,0)'],
    [0.3, 'rgba(255,255,255,0)'],
    [0.38, 'rgba(255,255,255,0.5)'],
    [0.46, 'rgba(255,255,255,0.08)'],
    [0.6, 'rgba(255,255,255,0)'],
    [1, 'rgba(255,255,255,0)'],
  ])
  const bandTex = makeTex([
    [0, 'rgba(255,255,255,0.42)'],
    [0.18, 'rgba(255,255,255,0.18)'],
    [0.42, 'rgba(255,255,255,0.05)'],
    [1, 'rgba(255,255,255,0)'],
  ])

  /* ---- 星尘（范围随宇宙尺寸适配） ---- */
  let dust: THREE.Points | null = null
  function buildDust(vs: number) {
    if (dust) {
      world.remove(dust)
      dust.geometry.dispose()
      ;(dust.material as THREE.Material).dispose()
    }
    const count = 380
    const posArr = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2
      const phi = Math.acos(2 * Math.random() - 1)
      const radius = (16 + Math.random() * 52) * vs
      posArr[i * 3] = radius * Math.sin(phi) * Math.cos(theta)
      posArr[i * 3 + 1] = radius * Math.cos(phi) * 0.62
      posArr[i * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta)
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(posArr, 3))
    dust = new THREE.Points(
      geo,
      new THREE.PointsMaterial({
        map: starTex,
        size: 0.28 * Math.sqrt(vs),
        color: 0xe7d8c6,
        transparent: true,
        opacity: 0.22,
        depthWrite: false,
        sizeAttenuation: true,
      }),
    )
    dust.renderOrder = 0
    world.add(dust)
  }
  buildDust(1)

  const pickGeo = new THREE.SphereGeometry(1, 8, 8)
  const pickMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false })

  /* ---- 动态内容容器 ---- */
  const nodes: NodeLive[] = []
  const nodeById = new Map<string, NodeLive>()
  const picks: THREE.Object3D[] = []
  let mistGroups: MistGroup[] = []
  let focusLines: FocusLine[] = []
  const pulses: Pulse[] = []
  const disposables: Array<{ dispose(): void }> = []

  /* ---- 流光粒子带（原版保留） ---- */
  const bandMax = 4096
  function makeRibbon(size: number) {
    const posArr = new Float32Array(bandMax * 3)
    const colArr = new Float32Array(bandMax * 3)
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(posArr, 3))
    geo.setAttribute('color', new THREE.BufferAttribute(colArr, 3))
    geo.setDrawRange(0, 0)
    const points = new THREE.Points(
      geo,
      new THREE.PointsMaterial({
        map: bandTex,
        size,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        vertexColors: true,
        sizeAttenuation: true,
      }),
    )
    points.frustumCulled = false
    points.renderOrder = 2
    world.add(points)
    return { pos: posArr, col: colArr, geo, points }
  }
  const winRibbon = makeRibbon(6.8)
  const bidRibbon = makeRibbon(4.6)
  let bandFade = 0

  /* ---- 曲线工具（原版 pointOn） ---- */
  const side = new THREE.Vector3()
  const upV = new THREE.Vector3(0, 1, 0)
  const alt = new THREE.Vector3(1, 0, 0)
  const scratch = new THREE.Vector3()

  function pointOn(a: THREE.Vector3, b: THREE.Vector3, t: number, ida: string, idb: string, out: THREE.Vector3) {
    side.crossVectors(scratch.copy(b).sub(a), upV)
    if (side.lengthSq() < 1e-6) side.crossVectors(scratch, alt)
    side.normalize()
    const sign = hash(ida + idb) > 0.5 ? 1 : -1
    const bend = (2.4 + hash(`${ida}|${idb}`) * 2.1) * sign
    const mx = (a.x + b.x) / 2 + side.x * bend
    const my = (a.y + b.y) / 2 + side.y * bend
    const mz = (a.z + b.z) / 2 + side.z * bend
    const u = 1 - t
    out.set(
      u * u * a.x + 2 * u * t * mx + t * t * b.x,
      u * u * a.y + 2 * u * t * my + t * t * b.y,
      u * u * a.z + 2 * u * t * mz + t * t * b.z,
    )
  }

  /* ---- 相机适配 ---- */
  let cameraSeeded = false
  function fitCamera(maxR: number) {
    controls.minDistance = maxR * 0.55
    controls.maxDistance = maxR * 4.2
    camera.far = Math.max(2000, maxR * 8)
    camera.updateProjectionMatrix()
    if (!cameraSeeded) {
      controls.target.set(maxR * 0.1, 0, 0)
      const eye = new THREE.Vector3(homeEye.x, homeEye.y, homeEye.z).normalize().multiplyScalar(maxR * 1.95)
      camera.position.copy(controls.target).add(eye)
      cameraSeeded = true
    }
  }

  /* ---- 重建（数据变更） ---- */
  let rebuildSeq = 0
  let pendingReadyAt = 0

  function clearDynamic() {
    for (const n of nodes) {
      world.remove(n.sprite, n.haze, n.pick, n.labelObj)
      if (n.rankObj) world.remove(n.rankObj)
      ;(n.sprite.material as THREE.Material).dispose()
      ;(n.haze.material as THREE.Material).dispose()
    }
    nodes.length = 0
    nodeById.clear()
    picks.length = 0
    for (const g of mistGroups) {
      world.remove(g.points)
      g.points.geometry.dispose()
      g.mat.dispose()
    }
    mistGroups = []
    clearFocusLines()
    for (const p of pulses) {
      world.remove(p.sprite)
      ;(p.sprite.material as THREE.Material).dispose()
    }
    pulses.length = 0
    for (const d of disposables) d.dispose()
    disposables.length = 0
  }

  function clearFocusLines() {
    for (const fl of focusLines) {
      world.remove(fl.line, fl.tagObj)
      fl.line.geometry.dispose()
      ;(fl.line.material as THREE.Material).dispose()
    }
    focusLines = []
  }

  async function rebuild() {
    const my = ++rebuildSeq
    clearDynamic()
    const t0 = performance.now()
    // 让出一帧：叙事面板等纯 DOM 先渲染（"数字先出、星图后补"）
    await new Promise((r) => setTimeout(r, 0))
    if (my !== rebuildSeq) return

    const placed = layoutGraph(props.nodes, props.edges)
    const placeMap = new Map(placed.map((p) => [p.id, p]))
    const vs = visualScale(placed)
    ;(world.fog as THREE.FogExp2).density = 0.007 / vs
    buildDust(vs)

    let maxR = 8
    for (const p of placed) maxR = Math.max(maxR, Math.hypot(p.x, p.y, p.z))
    fitCamera(maxR)

    for (const body of props.nodes) {
      const p = placeMap.get(body.id)
      if (!p) continue
      const base = new THREE.Vector3(p.x, p.y, p.z)
      const sprite = new THREE.Sprite(
        new THREE.SpriteMaterial({
          map: body.kind === 'buyer' ? cloudTex : body.kind === 'project' ? discTex : starTex,
          color: tint[body.kind],
          transparent: true,
          depthWrite: false,
          opacity: innerOp[body.kind],
        }),
      )
      sprite.scale.setScalar(body.kind === 'buyer' ? p.scale * 0.42 : body.kind === 'project' ? p.scale * 1.55 : p.scale * 0.72)
      sprite.position.copy(base)
      sprite.renderOrder = 4
      sprite.frustumCulled = false
      world.add(sprite)

      const haze = new THREE.Sprite(
        new THREE.SpriteMaterial({
          map: cloudTex,
          color: tint[body.kind],
          transparent: true,
          depthWrite: false,
          opacity: hazeOp[body.kind],
        }),
      )
      haze.scale.setScalar(p.scale * hazeMul[body.kind])
      haze.position.copy(base)
      haze.renderOrder = 3
      haze.frustumCulled = false
      world.add(haze)

      const pick = new THREE.Mesh(pickGeo, pickMat)
      pick.scale.setScalar(Math.max(0.9, p.scale * 0.34))
      pick.position.copy(base)
      pick.userData.id = body.id
      world.add(pick)
      picks.push(pick)

      const label = document.createElement('div')
      label.className = 'star-label'
      label.textContent = body.label
      const labelObj = new CSS2DObject(label)
      labelObj.position.copy(base)
      world.add(labelObj)

      const live: NodeLive = {
        id: body.id,
        kind: body.kind,
        base,
        phase: hash(body.id) * Math.PI * 2,
        opacity: 1,
        target: 1,
        scale: p.scale,
        labelOp: 1,
        sprite,
        haze,
        pick,
        labelObj,
        rankObj: null,
      }
      nodes.push(live)
      nodeById.set(body.id, live)
    }

    // 静态边 → 粒子雾：按哈希分 6 组，亮度包络（中段亮两端暗）烧进顶点色，
    // 帧循环只动各组透明度做呼吸，buffer 构建一次、零帧开销。
    if (props.edges.length > 0) {
      const buckets: Array<{ pos: number[]; col: number[] }> = Array.from(
        { length: MIST_GROUPS },
        () => ({ pos: [], col: [] }),
      )
      const endA = new THREE.Vector3()
      const endB = new THREE.Vector3()
      for (const e of props.edges) {
        const pa = placeMap.get(e.a)
        const pb = placeMap.get(e.b)
        if (!pa || !pb) continue
        endA.set(pa.x, pa.y, pa.z)
        endB.set(pb.x, pb.y, pb.z)
        const h = hash(`${e.a}~${e.b}`)
        const g = Math.min(MIST_GROUPS - 1, Math.floor(h * MIST_GROUPS))
        const bucket = buckets[g]!
        const warm = e.role !== 'bid'
        const gain = 0.32 + h * 0.28
        const cr = warm ? 1.0 * gain : 0.62 * gain
        const cg = warm ? 0.85 * gain : 0.7 * gain
        const cb = warm ? 0.66 * gain : 0.82 * gain
        for (let i = 0; i < MIST_SAMPLES; i++) {
          const t = i / MIST_SAMPLES
          pointOn(endA, endB, t, e.a, e.b, scratch)
          // 原版 sprinkle 的亮度曲线：包络 × 波动，雾有明暗肌理
          const env = Math.sin(Math.PI * t)
          const wave = 0.28 + 0.72 * Math.pow(Math.sin(t * Math.PI * 4 + h * Math.PI * 8), 2)
          const bright = env * wave * 0.45
          bucket.pos.push(scratch.x, scratch.y, scratch.z)
          bucket.col.push(bright * cr, bright * cg, bright * cb)
        }
      }
      buckets.forEach((bucket, g) => {
        if (!bucket.pos.length) return
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(bucket.pos), 3))
        geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(bucket.col), 3))
        const mat = new THREE.PointsMaterial({
          map: bandTex,
          size: 3.2 * vs,
          transparent: true,
          opacity: 0,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
          vertexColors: true,
          sizeAttenuation: true,
        })
        const points = new THREE.Points(geo, mat)
        points.frustumCulled = false
        points.renderOrder = 1
        world.add(points)
        mistGroups.push({ points, mat, phase: (g / MIST_GROUPS) * Math.PI * 2, op: 0 })
      })
    }

    applyEmphasis()
    pendingReadyAt = t0
  }

  /* ---- 强调态（聚焦/高亮/排名，无需重建） ---- */
  let relatedIds = new Set<string>()
  let focusEdgeList: GraphEdge[] = []

  function applyEmphasis() {
    const focus = props.focusId && nodeById.has(props.focusId) ? props.focusId : null
    const highlights = new Set(props.highlightIds.filter((id) => nodeById.has(id)))

    // 相关集合：聚焦点 + 一跳 + 项目一跳的各方（原版 focusOf 的 1.5 跳语义）
    relatedIds = new Set<string>()
    focusEdgeList = []
    if (focus) {
      relatedIds.add(focus)
      const projHop = new Set<string>()
      for (const e of props.edges) {
        if (e.a === focus || e.b === focus) {
          const other = e.a === focus ? e.b : e.a
          relatedIds.add(other)
          focusEdgeList.push(e)
          if (nodeById.get(other)?.kind === 'project') projHop.add(other)
        }
      }
      if (projHop.size) {
        for (const e of props.edges) {
          if (projHop.has(e.a) || projHop.has(e.b)) {
            focusEdgeList.push(e)
            relatedIds.add(e.a)
            relatedIds.add(e.b)
          }
        }
      }
    }
    for (const id of highlights) relatedIds.add(id)

    const dimming = props.dimOthers && (focus !== null || highlights.size > 0)
    for (const n of nodes) {
      n.target = !dimming || relatedIds.has(n.id) ? 1 : 0.18
    }

    // 标签 LOD：聚焦/高亮必显，其余按权重补足 80 个
    const mustShow = new Set([...(focus ? [focus] : []), ...highlights])
    const weightOf = new Map(props.nodes.map((n) => [n.id, n.weight]))
    if (nodes.length > LABEL_CAP) {
      const rest = nodes
        .filter((n) => !mustShow.has(n.id))
        .sort((a, b) => (weightOf.get(b.id) ?? 0) - (weightOf.get(a.id) ?? 0))
      const allow = new Set(rest.slice(0, Math.max(0, LABEL_CAP - mustShow.size)).map((n) => n.id))
      for (const n of nodes) n.labelObj.visible = mustShow.has(n.id) || allow.has(n.id)
    } else {
      for (const n of nodes) n.labelObj.visible = true
    }

    // 聚焦邻域边：独立曲线 + 边标签（仅这些边有 DOM）
    clearFocusLines()
    if (focus) {
      for (const e of focusEdgeList) {
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array((SEG + 1) * 3), 3))
        const line = new THREE.Line(
          geo,
          new THREE.LineBasicMaterial({ color: 0xf4e6d4, transparent: true, opacity: 0.18, depthWrite: false }),
        )
        line.frustumCulled = false
        line.renderOrder = 1
        world.add(line)
        const tag = document.createElement('div')
        tag.className = 'star-edge'
        tag.textContent = EDGE_ROLE_LABEL[e.role] ?? ''
        const tagObj = new CSS2DObject(tag)
        world.add(tagObj)
        focusLines.push({ a: e.a, b: e.b, role: e.role, line, tagObj })
      }
    }

    // 排名徽标（仅 highlighted 且有 rank 的节点）
    for (const n of nodes) {
      if (n.rankObj) {
        world.remove(n.rankObj)
        n.rankObj = null
      }
      const rank = props.ranks[n.id]
      if (rank) {
        const el2 = document.createElement('div')
        el2.className = 'star-rank'
        el2.textContent = `#${rank}`
        const obj = new CSS2DObject(el2)
        obj.position.copy(n.base)
        world.add(obj)
        n.rankObj = obj
      }
    }

    // 一次性脉冲环（高亮节点；reduced-motion 时跳过）
    for (const p of pulses) {
      world.remove(p.sprite)
      ;(p.sprite.material as THREE.Material).dispose()
    }
    pulses.length = 0
    if (!reduce) {
      for (const id of highlights) {
        const n = nodeById.get(id)
        if (!n) continue
        const sprite = new THREE.Sprite(
          new THREE.SpriteMaterial({
            map: ringTex,
            color: 0xffce94,
            transparent: true,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
            opacity: 0.9,
          }),
        )
        sprite.position.copy(n.base)
        sprite.renderOrder = 5
        world.add(sprite)
        pulses.push({ sprite, t: 0, size: Math.max(2.2, n.scale * 0.9) })
      }
    }
  }

  /* ---- 交互（raycast 节流） ---- */
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  let downX = 0
  let downY = 0
  let lastCast = 0

  function cast(e: PointerEvent) {
    const rect = renderer.domElement.getBoundingClientRect()
    pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
    pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    raycaster.setFromCamera(pointer, camera)
    return raycaster.intersectObjects(picks, false).find((hit) => hit.object.visible)
  }

  function onDown(e: PointerEvent) {
    downX = e.clientX
    downY = e.clientY
  }
  function onUp(e: PointerEvent) {
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 6) return
    const hit = cast(e)
    emit('select', hit ? (hit.object.userData.id as string) : null)
  }
  function onMove(e: PointerEvent) {
    if (e.buttons) return
    const now = performance.now()
    if (now - lastCast < 60) return
    lastCast = now
    const hit = cast(e)
    emit('hover', hit ? (hit.object.userData.id as string) : null)
  }
  renderer.domElement.addEventListener('pointerdown', onDown)
  renderer.domElement.addEventListener('pointerup', onUp)
  renderer.domElement.addEventListener('pointermove', onMove)
  controls.addEventListener('start', () => {
    renderer.domElement.style.cursor = 'grabbing'
  })
  controls.addEventListener('end', () => {
    renderer.domElement.style.cursor = 'grab'
  })

  function resize() {
    const w = el.clientWidth
    const h = el.clientHeight
    if (!w || !h) return
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h, false)
    labels.setSize(w, h)
  }
  resize()
  const ro = new ResizeObserver(resize)
  ro.observe(el)

  /* ---- 帧循环 ---- */
  const clock = new THREE.Clock()
  let raf = 0
  const endA = new THREE.Vector3()
  const endB = new THREE.Vector3()

  function frame() {
    raf = requestAnimationFrame(frame)
    const dt = Math.min(0.05, clock.getDelta())
    const time = clock.elapsedTime
    controls.update()

    for (const node of nodes) {
      node.opacity += (node.target - node.opacity) * (1 - Math.exp(-dt * 2.4))
      const drift = reduce ? 0 : 1
      const x = node.base.x + Math.sin(time * 0.22 + node.phase) * 0.18 * drift
      const y = node.base.y + Math.cos(time * 0.18 + node.phase) * 0.12 * drift
      const z = node.base.z + Math.sin(time * 0.16 + node.phase * 1.4) * 0.14 * drift
      const breath = reduce ? 1 : 1 + Math.sin(time * 0.28 + node.phase) * 0.028
      node.sprite.position.set(x, y, z)
      node.haze.position.set(x, y, z)
      node.pick.position.set(x, y, z)
      node.labelObj.position.set(x, y, z)
      if (node.rankObj) node.rankObj.position.set(x, y + node.scale * 0.5, z)
      const shown = node.kind === 'buyer' ? node.scale * 0.42 : node.kind === 'project' ? node.scale * 1.55 : node.scale * 0.72
      node.sprite.scale.setScalar(shown * breath)
      node.haze.scale.setScalar(node.scale * hazeMul[node.kind] * breath)
      ;(node.sprite.material as THREE.SpriteMaterial).opacity = innerOp[node.kind] * node.opacity
      ;(node.haze.material as THREE.SpriteMaterial).opacity = hazeOp[node.kind] * node.opacity
      node.pick.visible = node.opacity > 0.32
      // 标签随节点一起淡化（避免"星淡了字还亮着"）
      const lop = Math.max(0, node.opacity)
      if (Math.abs(lop - node.labelOp) > 0.01) {
        ;(node.labelObj.element as HTMLDivElement).style.opacity = String(lop)
        node.labelOp = lop
      }
    }

    // 雾分组呼吸：错相正弦 ^4，任意时刻只有 1–2 组亮起；聚焦时整体压暗
    for (const g of mistGroups) {
      const breath = reduce
        ? 0.1
        : 0.02 + 0.38 * Math.pow(0.5 + 0.5 * Math.sin(time * 0.785 + g.phase), 4)
      const targetOp = props.focusId ? 0.015 : breath
      g.op += (targetOp - g.op) * (1 - Math.exp(-dt * 2.2))
      g.mat.opacity = g.op
    }

    // 脉冲环：0.6s 扩散一次后移除
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i]!
      p.t += dt / 0.6
      if (p.t >= 1) {
        world.remove(p.sprite)
        ;(p.sprite.material as THREE.Material).dispose()
        pulses.splice(i, 1)
        continue
      }
      const ease = 1 - Math.pow(1 - p.t, 2)
      p.sprite.scale.setScalar(p.size * (0.8 + ease * 1.8))
      ;(p.sprite.material as THREE.SpriteMaterial).opacity = 0.9 * (1 - p.t)
    }

    // 聚焦邻域边：每帧只更新这些曲线
    const focusActive = focusLines.length > 0
    for (const fl of focusLines) {
      const na = nodeById.get(fl.a)
      const nb = nodeById.get(fl.b)
      if (!na || !nb) continue
      endA.copy(na.sprite.position)
      endB.copy(nb.sprite.position)
      const attr = fl.line.geometry.getAttribute('position') as THREE.BufferAttribute
      const arr = attr.array as Float32Array
      for (let i = 0; i <= SEG; i++) {
        pointOn(endA, endB, i / SEG, fl.a, fl.b, scratch)
        arr[i * 3] = scratch.x
        arr[i * 3 + 1] = scratch.y
        arr[i * 3 + 2] = scratch.z
      }
      attr.needsUpdate = true
      pointOn(endA, endB, 0.5, fl.a, fl.b, scratch)
      fl.tagObj.position.copy(scratch)
      const el3 = fl.tagObj.element as HTMLDivElement
      el3.style.opacity = String(Math.min(na.opacity, nb.opacity) * 2.2)
      el3.style.color =
        fl.role === 'win' ? 'rgba(255, 206, 148, 0.96)' : fl.role === 'buy' ? 'rgba(236, 220, 196, 0.82)' : 'rgba(214, 208, 198, 0.48)'
    }

    // 流光粒子带（原版 sprinkle/publish，仅聚焦态）
    bandFade += ((focusActive ? 1 : 0) - bandFade) * (1 - Math.exp(-dt * 2.2))

    function sprinkle(
      posArr: Float32Array,
      colArr: Float32Array,
      start: number,
      edge: GraphEdge,
      samples: number,
      gain: number,
      cr: number,
      cg: number,
      cb: number,
    ) {
      let n2 = start
      const na = nodeById.get(edge.a)
      const nb = nodeById.get(edge.b)
      if (!na || !nb) return n2
      endA.copy(na.sprite.position)
      endB.copy(nb.sprite.position)
      for (let i = 0; i < samples; i++) {
        if (n2 >= bandMax) break
        const along = i / samples
        const phase = reduce ? along : (along + time * 0.065) % 1
        pointOn(endA, endB, phase, edge.a, edge.b, scratch)
        const env = Math.sin(Math.PI * ((phase + 1) % 1))
        const wave = 0.28 + 0.72 * Math.pow(Math.sin(phase * Math.PI * 4 - time * 0.9), 2)
        const bright = env * wave * bandFade * gain
        posArr[n2 * 3] = scratch.x
        posArr[n2 * 3 + 1] = scratch.y
        posArr[n2 * 3 + 2] = scratch.z
        colArr[n2 * 3] = bright * cr
        colArr[n2 * 3 + 1] = bright * cg
        colArr[n2 * 3 + 2] = bright * cb
        n2++
      }
      return n2
    }

    function publish(ribbon: ReturnType<typeof makeRibbon>, count: number) {
      ribbon.geo.setDrawRange(0, count)
      ;(ribbon.geo.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true
      ;(ribbon.geo.getAttribute('color') as THREE.BufferAttribute).needsUpdate = true
      ribbon.points.visible = count > 0
    }

    let winCount = 0
    let bidCount = 0
    if (bandFade > 0.03) {
      for (const edge of focusEdgeList) {
        if (edge.role === 'win') {
          winCount = sprinkle(winRibbon.pos, winRibbon.col, winCount, edge, 96, 1.08, 1.16, 0.72, 0.36)
        } else if (edge.role === 'bid') {
          bidCount = sprinkle(bidRibbon.pos, bidRibbon.col, bidCount, edge, 120, 0.34, 0.78, 0.76, 0.72)
        } else if (edge.role === 'supply') {
          bidCount = sprinkle(bidRibbon.pos, bidRibbon.col, bidCount, edge, 90, 0.22, 0.64, 0.6, 0.54)
        } else if (edge.role === 'buy') {
          bidCount = sprinkle(bidRibbon.pos, bidRibbon.col, bidCount, edge, 100, 0.5, 0.94, 0.86, 0.68)
        }
      }
    }
    publish(winRibbon, winCount)
    publish(bidRibbon, bidCount)

    renderer.render(world, camera)
    labels.render(world, camera)

    if (pendingReadyAt) {
      emit('ready', Math.round(performance.now() - pendingReadyAt))
      pendingReadyAt = 0
    }
  }
  raf = requestAnimationFrame(frame)

  /* ---- 数据与强调态监听 ---- */
  watch(() => [props.nodes, props.edges], () => void rebuild(), { deep: false })
  watch(
    () => [props.focusId, props.highlightIds, props.ranks, props.dimOthers],
    () => applyEmphasis(),
    { deep: false },
  )
  void rebuild()

  cleanup = () => {
    cancelAnimationFrame(raf)
    ro.disconnect()
    renderer.domElement.removeEventListener('pointerdown', onDown)
    renderer.domElement.removeEventListener('pointerup', onUp)
    renderer.domElement.removeEventListener('pointermove', onMove)
    rebuildSeq++
    controls.dispose()
    pickGeo.dispose()
    pickMat.dispose()
    starTex.dispose()
    discTex.dispose()
    bandTex.dispose()
    cloudTex.dispose()
    ringTex.dispose()
    if (dust) {
      dust.geometry.dispose()
      ;(dust.material as THREE.Material).dispose()
    }
    winRibbon.geo.dispose()
    bidRibbon.geo.dispose()
    ;(winRibbon.points.material as THREE.Material).dispose()
    ;(bidRibbon.points.material as THREE.Material).dispose()
    clearDynamic()
    renderer.dispose()
    labels.domElement.remove()
    renderer.domElement.remove()
  }
})

onUnmounted(() => cleanup())
</script>

<template>
  <div ref="host" class="starmap-host" aria-label="关系星图，拖拽旋转，滚轮缩放" />
</template>

<style scoped>
.starmap-host {
  position: absolute;
  inset: 0;
}
</style>
