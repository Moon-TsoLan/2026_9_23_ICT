<script setup lang="ts">
/**
 * StarMap —— 3D 关系星图。
 *
 * 视觉内核沿用旧版，未改动：带核心的光球（sprite + haze）、雾状连线（6 组呼吸）、
 * 流光粒子带、力导布局（useGraphLayout）。这一版只动三件事：
 *  1. 取景：相机 seed 只在拿到数据后发生，数据变化做补间；对外暴露 frame() / flyTo()。
 *  2. 标签：从"每颗星硬贴全名"改成**屏幕资源制**——视野范围过滤 + 8 向锚位 + 两行断行
 *     + 短名/包号 + 答案集强制显示 + 滞回防抖 + 字号随距离衰减。
 *  3. 编码：五类节点用亮度与尺寸拉开层级（项目金最亮最大 / 中标银 / 供应商铜 / 投标灰小 /
 *     采购方暗金大圆盘），光球结构本身不变。
 */
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import * as THREE from 'three'
import { onMounted, onUnmounted, ref, watch } from 'vue'
import type { LayoutMode } from '@/composables/useGraphLayout'
import { hash, homeEye, layoutGraph, visualScale } from '@/composables/useGraphLayout'
import type { DisplayKind, GraphEdge, GraphNode, NodeKind } from '@/types/graph'
import { DISPLAY_KIND, EDGE_ROLE_LABEL } from '@/types/graph'
import { disambiguate, rankMark, wrapTwo } from '@/utils/label'

const props = withDefaults(
  defineProps<{
    nodes: GraphNode[]
    edges: GraphEdge[]
    focusId?: string | null
    highlightIds?: string[]
    ranks?: Record<string, number>
    dimOthers?: boolean
    /** 图例过滤：被关掉的类别不画也不标注 */
    hiddenKinds?: DisplayKind[]
    /** 排布模式：disc = 原来的扁平圆盘，sphere = 球面星团（画面效果由使用者确认） */
    layoutMode?: LayoutMode
  }>(),
  { focusId: null, highlightIds: () => [], ranks: () => ({}), dimOthers: true, hiddenKinds: () => [], layoutMode: 'disc' },
)

const emit = defineEmits<{
  (e: 'select', id: string | null): void
  (e: 'hover', id: string | null, pt: { x: number; y: number } | null): void
  (e: 'ready', ms: number): void
  (e: 'labels', shown: number, candidates: number): void
}>()

const host = ref<HTMLDivElement | null>(null)

/* ---- 三类节点：色相三分 + 尺寸三分 + 各自的核心纹理 ----
   采购单位 = 大蓝日（数量最少、最大、带蓝晕）
   项目     = 金星（数量最多、中等、暖金）
   供应商   = 小白点（最小但实心，靠"亮而小"而不是"暗而糊"区分）
   之前采购单位用的是最淡的云纹理（峰值 0.14），实际看到的是它周围那圈金色项目。 */
const tint: Record<DisplayKind, string> = {
  buyer: '#54c4ff',
  project: '#ffc861',
  supplier: '#eef3fb',
}
const innerOp: Record<DisplayKind, number> = { buyer: 1, project: 0.95, supplier: 0.92 }
const hazeOp: Record<DisplayKind, number> = { buyer: 0.4, project: 0.2, supplier: 0.07 }
const hazeMul: Record<DisplayKind, number> = { buyer: 2.3, project: 2.0, supplier: 1.15 }
const sizeMul: Record<DisplayKind, number> = { buyer: 2.3, project: 1.35, supplier: 0.95 }
/** 核心纹理：采购与供应用紧核（小也看得见），项目用盘状（大面积暖金） */
const coreOf: Record<DisplayKind, 'core' | 'disc'> = { buyer: 'core', project: 'disc', supplier: 'core' }

/* ---- 标签系统参数 ---- */
const CELL = 8 // 屏幕占位网格边长（px）
const LABEL_INK_BUDGET = 0.045 // 文字底板最多占画布 4.5%（改造前约 30%）
const OVERVIEW_MIN_KIND = 200 // 节点数超过它=总览氛围模式，只给 hub 让字
const FONT_NEAR = 13
const FONT_FAR = 10.5
const SEG = 32 // 聚焦邻域曲线的细分（沿用旧版）
const MIST_GROUPS = 6 // 沿用旧版：任意时刻只有 1–2 个天区在亮
const MIST_PX = 3.2 // 雾粒子尺寸（随 vs 缩放，保持旧观感）
const BACKBONE_OP = 0.5 // 常驻骨架不透明度（不参与呼吸）
const STAR_OBSTACLE_PX = 11 // 屏幕半径大于这个的星才给文字"让位"（半透明底板负责其余可读性）/** 8 个候选锚位：文字朝四周放射，不再横向排成一串 */
const ANCHORS = [
  { sx: 1, sy: -1 }, { sx: 1, sy: 1 }, { sx: -1, sy: -1 }, { sx: -1, sy: 1 },
  { sx: 1, sy: 0 }, { sx: -1, sy: 0 }, { sx: 0, sy: -1 }, { sx: 0, sy: 1 },
] as const

type NodeLive = {
  id: string
  kind: NodeKind
  /** 图上归属的展示类别（winner/bidder/vendor 都算 supplier） */
  dk: DisplayKind
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
  labelEl: HTMLDivElement
  /** 标注状态 */
  text: string
  prio: number
  mustShow: boolean
  /** 滞回计数：+2 才允许出现，-2 才消失，避免旋转时文字闪跳 */
  pass: number
  shown: boolean
  dir: number
  gapPx: number
  dist: number
  /** 是否曾经显示过：没显示过的第一次直接给，避免滞回把首屏卡死 */
  everShown: boolean
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

/** 取景/定位的真实实现在 onMounted 里（需要 camera 与节点表），这里做转接 */
let frameFn: (animated?: boolean) => void = () => {}
let flyToFn: (id: string) => void = () => {}
let hasNodeFn: (id: string) => boolean = () => false

defineExpose({
  frame: (animated?: boolean) => frameFn(animated),
  flyTo: (id: string) => flyToFn(id),
  hasNode: (id: string) => hasNodeFn(id),
})

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
    [0, 'rgba(255,255,255,0.98)'],
    [0.05, 'rgba(255,255,255,0.34)'],
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
  // 紧核纹理：中心实心、衰减快。小星体用柔边纹理会在远距离上糊成不可见
  const coreTex = makeTex([
    [0, 'rgba(255,255,255,1)'],
    [0.07, 'rgba(255,255,255,0.72)'],
    [0.18, 'rgba(255,255,255,0.18)'],
    [0.45, 'rgba(255,255,255,0.04)'],
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
  const shortById = new Map<string, string>()
  const picks: THREE.Object3D[] = []
  let mistGroups: MistGroup[] = []
  let backbone: { points: THREE.Points; mat: THREE.PointsMaterial } | null = null
  let focusLines: FocusLine[] = []
  let winRing: THREE.Sprite | null = null
  let winRingId = ''
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
  const winRibbon = makeRibbon(5.6)
  const bidRibbon = makeRibbon(3.9)
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

  /* ---- 取景：seed 只在拿到数据后发生，切换做补间 ---- */
  let graphRadius = 8
  let camSeeded = false
  let camTween: { fromP: THREE.Vector3; toP: THREE.Vector3; fromT: THREE.Vector3; toT: THREE.Vector3; t: number } | null = null
  let lastInteract = 0
  let labelDirty = true
  let lastDeclutter = 0

  function homePose() {
    const target = new THREE.Vector3(graphRadius * 0.1, 0, 0)
    const eye = new THREE.Vector3(homeEye.x, homeEye.y, homeEye.z).normalize().multiplyScalar(graphRadius * 1.95)
    return { target, pos: target.clone().add(eye) }
  }

  function frame(animated = true) {
    const { target, pos } = homePose()
    controls.minDistance = graphRadius * 0.55
    controls.maxDistance = graphRadius * 4.2
    camera.far = Math.max(2000, graphRadius * 8)
    camera.updateProjectionMatrix()
    if (!animated || reduce) {
      controls.target.copy(target)
      camera.position.copy(pos)
      camTween = null
      labelDirty = true
      return
    }
    camTween = { fromP: camera.position.clone(), toP: pos, fromT: controls.target.clone(), toT: target, t: 0 }
  }

  /** 定位到某个节点：相机推到它跟前，并把视点交给它（搜索即定位） */
  function flyTo(id: string) {
    const n = nodeById.get(id)
    if (!n) return
    const d = Math.max(graphRadius * 0.92, 30) // 太近会让聚焦态的流光带糊成一片白光
    const dir = camera.position.clone().sub(controls.target).normalize()
    const target = n.base.clone()
    const pos = target.clone().add(dir.multiplyScalar(d))
    if (reduce) {
      controls.target.copy(target)
      camera.position.copy(pos)
      camTween = null
      labelDirty = true
      return
    }
    camTween = { fromP: camera.position.clone(), toP: pos, fromT: controls.target.clone(), toT: target, t: 0 }
  }

  frameFn = frame
  flyToFn = flyTo
  hasNodeFn = (id: string) => nodeById.has(id)

  /* ---- 重建（数据变更） ---- */
  let rebuildSeq = 0
  let pendingReadyAt = 0

  function clearDynamic() {
    for (const n of nodes) {
      world.remove(n.sprite, n.haze, n.pick, n.labelObj)
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
    if (backbone) {
      world.remove(backbone.points)
      backbone.points.geometry.dispose()
      backbone.mat.dispose()
      backbone = null
    }
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
    winRingId = ''
    if (winRing) {
      world.remove(winRing)
      ;(winRing.material as THREE.Material).dispose()
      winRing = null
    }
  }

  /** 雾分组按"天区"而不是随机哈希：一次呼吸扫过一片，而不是全屏碎片闪烁 */
  function sectorOf(mid: THREE.Vector3, groups: number) {
    const len = mid.length() || 1
    const az = Math.atan2(mid.z, mid.x) // -π..π
    const el = Math.asin(THREE.MathUtils.clamp(mid.y / len, -1, 1)) // -π/2..π/2
    const a = Math.floor((((az + Math.PI) / (Math.PI * 2)) * 3 + 0.001) % 3)
    return (a * 2 + (el > 0 ? 1 : 0)) % groups
  }

  async function rebuild() {
    const my = ++rebuildSeq
    clearDynamic()
    const t0 = performance.now()
    // 让出一帧：叙事面板等纯 DOM 先渲染（"数字先出、星图后补"）
    await new Promise((r) => setTimeout(r, 0))
    if (my !== rebuildSeq) return

    const placed = layoutGraph(props.nodes, props.edges, props.layoutMode)
    shortById.clear()
    for (const [id, t] of disambiguate(props.nodes)) shortById.set(id, t)
    const placeMap = new Map(placed.map((p) => [p.id, p]))
    const vs = visualScale(placed)
    ;(world.fog as THREE.FogExp2).density = 0.007 / vs
    buildDust(vs)

    let maxR = 8
    for (const p of placed) maxR = Math.max(maxR, Math.hypot(p.x, p.y, p.z))
    graphRadius = maxR
    // 只有真的拿到数据才允许 seed，否则空图会把机位锁死在 maxR=8
    if (!camSeeded && placed.length) {
      camSeeded = true
      frame(false)
    } else if (placed.length) {
      frame(true)
    }

    for (const body of props.nodes) {
      const p = placeMap.get(body.id)
      if (!p) continue
      const base = new THREE.Vector3(p.x, p.y, p.z)
      const sprite = new THREE.Sprite(
        new THREE.SpriteMaterial({
          map: coreOf[DISPLAY_KIND[body.kind]] === 'disc' ? discTex : coreTex,
          color: tint[DISPLAY_KIND[body.kind]],
          transparent: true,
          depthWrite: false,
          opacity: innerOp[DISPLAY_KIND[body.kind]],
        }),
      )
      sprite.scale.setScalar(p.scale * sizeMul[DISPLAY_KIND[body.kind]])
      sprite.position.copy(base)
      sprite.renderOrder = 4
      sprite.frustumCulled = false
      world.add(sprite)

      const haze = new THREE.Sprite(
        new THREE.SpriteMaterial({
          map: cloudTex,
          color: tint[DISPLAY_KIND[body.kind]],
          transparent: true,
          depthWrite: false,
          opacity: hazeOp[DISPLAY_KIND[body.kind]],
        }),
      )
      haze.scale.setScalar(p.scale * hazeMul[DISPLAY_KIND[body.kind]])
      haze.position.copy(base)
      haze.renderOrder = 3
      haze.frustumCulled = false
      world.add(haze)

      const pick = new THREE.Mesh(pickGeo, pickMat)
      pick.scale.setScalar(Math.max(2.2, p.scale * 0.62))
      pick.position.copy(base)
      pick.userData.id = body.id
      world.add(pick)
      picks.push(pick)

      // 标签：两行结构 + 排名前缀，锚位与字号由 declutter 决定
      const label = document.createElement('div')
      label.className = 'star-label'
      const l1 = document.createElement('i')
      const l2 = document.createElement('i')
      label.append(l1, l2)
      const labelObj = new CSS2DObject(label)
      labelObj.visible = false // 由 declutter 决定谁配出现
      labelObj.position.copy(base)
      world.add(labelObj)

      const live: NodeLive = {
        id: body.id,
        kind: body.kind,
        dk: DISPLAY_KIND[body.kind],
        base,
        phase: hash(body.id) * Math.PI * 2,
        opacity: 1,
        target: 1,
        scale: p.scale,
        labelOp: -1,
        sprite,
        haze,
        pick,
        labelObj,
        labelEl: label,
        text: '',
        prio: 999,
        mustShow: false,
        pass: 0,
        shown: false,
        dir: 0,
        gapPx: 0,
        dist: 0,
        everShown: false,
      }
      nodes.push(live)
      nodeById.set(body.id, live)
    }

    /* 静态边 → 粒子雾：亮度包络（中段亮两端暗）烧进顶点色，帧循环只动各组透明度。
       与旧版的差别只有两点：分组按天区（见 sectorOf）、外加一层不参与呼吸的常驻骨架。 */
    if (props.edges.length > 0) {
      const samples = Math.max(6, Math.min(36, Math.round(60000 / props.edges.length)))
      const buckets: Array<{ pos: number[]; col: number[] }> = Array.from({ length: MIST_GROUPS }, () => ({
        pos: [],
        col: [],
      }))
      const bb = { pos: [] as number[], col: [] as number[] }
      const endA = new THREE.Vector3()
      const endB = new THREE.Vector3()
      const mid = new THREE.Vector3()
      // 权重前若干的边进骨架层：信息量最大的关系常驻，长尾才呼吸
      const sorted = [...props.edges].sort((x, y) => y.weight - x.weight)
      const backboneSet = new Set(sorted.slice(0, Math.min(260, Math.round(props.edges.length * 0.06))))
      for (const e of props.edges) {
        const pa = placeMap.get(e.a)
        const pb = placeMap.get(e.b)
        if (!pa || !pb) continue
        endA.set(pa.x, pa.y, pa.z)
        endB.set(pb.x, pb.y, pb.z)
        const h = hash(`${e.a}~${e.b}`)
        const inBB = backboneSet.has(e)
        const g = inBB ? -1 : sectorOf(mid.addVectors(endA, endB).multiplyScalar(0.5), MIST_GROUPS)
        const bucket = inBB ? bb : buckets[Math.max(0, g)]!
        const warm = e.role !== 'bid'
        const gain = inBB ? 0.2 + h * 0.12 : 0.32 + h * 0.28
        const cr = warm ? 1.0 * gain : 0.62 * gain
        const cg = warm ? 0.85 * gain : 0.7 * gain
        const cb = warm ? 0.66 * gain : 0.82 * gain
        for (let i = 0; i < samples; i++) {
          const t = i / samples
          pointOn(endA, endB, t, e.a, e.b, scratch)
          const env = Math.sin(Math.PI * t)
          const wave = 0.28 + 0.72 * Math.pow(Math.sin(t * Math.PI * 4 + h * Math.PI * 8), 2)
          const bright = env * (inBB ? 1 : wave) * (inBB ? 0.5 : 0.45)
          bucket.pos.push(scratch.x, scratch.y, scratch.z)
          bucket.col.push(bright * cr, bright * cg, bright * cb)
        }
      }
      const mkPoints = (bucket: { pos: number[]; col: number[] }, opacity: number, size: number) => {
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(bucket.pos), 3))
        geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(bucket.col), 3))
        const mat = new THREE.PointsMaterial({
          map: bandTex,
          size,
          transparent: true,
          opacity,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
          vertexColors: true,
          sizeAttenuation: true,
        })
        const points = new THREE.Points(geo, mat)
        points.frustumCulled = false
        points.renderOrder = 1
        world.add(points)
        return { points, mat }
      }
      buckets.forEach((bucket, g) => {
        if (!bucket.pos.length) return
        const built = mkPoints(bucket, 0, MIST_PX * vs)
        mistGroups.push({ ...built, phase: (g / MIST_GROUPS) * Math.PI * 2, op: 0 })
      })
      if (bb.pos.length) backbone = mkPoints(bb, BACKBONE_OP, MIST_PX * vs * 0.8)
    }

    applyEmphasis()
    labelDirty = true
    pendingReadyAt = t0
  }


  /* ---- 强调态（聚焦/高亮/排名/图例过滤，无需重建） ---- */
  let relatedIds = new Set<string>()
  let focusEdgeList: GraphEdge[] = []

  function applyEmphasis() {
    const focus = props.focusId && nodeById.has(props.focusId) ? props.focusId : null
    const highlights = new Set(props.highlightIds.filter((id) => nodeById.has(id)))
    const hidden = new Set<DisplayKind>(props.hiddenKinds)

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
      n.target = hidden.has(n.dk) ? 0 : !dimming || relatedIds.has(n.id) ? 1 : 0.18
    }

    // 标签文字与优先级：答案集(榜单) > 聚焦邻域 > 高亮 > 采购方 > 中标 > 供应商 > 投标 > 项目
    const KIND_PRIO: Record<DisplayKind, number> = { buyer: 20, supplier: 24, project: 40 }
    const weightOf = new Map(props.nodes.map((n) => [n.id, n.weight]))
    const kindOf = new Map(props.nodes.map((n) => [n.id, n.kind]))
    const overviewMode = nodes.length > OVERVIEW_MIN_KIND
    for (const n of nodes) {
      const kind = kindOf.get(n.id) ?? n.kind
      const raw = shortById.get(n.id) ?? ''
      const mark = rankMark(props.ranks[n.id])
      n.text = raw ? mark + raw : ''
      const rank = props.ranks[n.id]
      let prio = KIND_PRIO[DISPLAY_KIND[kind]] ?? 30
      if (rank) prio = rank
      else if (focus && n.id === focus) prio = 5
      else if (focus && relatedIds.has(n.id)) prio = 8
      else if (highlights.has(n.id)) prio = 10
      else if (overviewMode && DISPLAY_KIND[kind] !== 'buyer') prio = 999 // 总览=氛围层：只给采购单位的星团命名，其余靠悬停
      n.prio = prio
      n.mustShow = !!rank || (!!focus && n.id === focus)
      if (!n.text || hidden.has(DISPLAY_KIND[kind]) || (n.target < 0.5 && !n.mustShow)) n.prio = 999
    }

    // 聚焦邻域边：独立曲线 + 角色标签（仅这些边有 DOM）
    clearFocusLines()
    if (focus) {
      for (const e of focusEdgeList) {
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array((SEG + 1) * 3), 3))
        const ROLE_LOOK: Record<string, { c: number; o: number }> = {
          win: { c: 0xffc861, o: 0.62 }, // 中标：金，最亮
          buy: { c: 0x79d0ff, o: 0.34 }, // 采购：冰蓝
          supply: { c: 0xc98a4b, o: 0.3 }, // 供货：铜
          bid: { c: 0x8ea6c4, o: 0.14 }, // 投标：冷灰，退到背景
        }
        const look = ROLE_LOOK[e.role] ?? ROLE_LOOK.bid!
        const line = new THREE.Line(
          geo,
          new THREE.LineBasicMaterial({ color: look.c, transparent: true, opacity: look.o, depthWrite: false }),
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

    // 选中的是项目：给它"本项目中标者"一枚常驻金环（身份来自边，不来自节点类别）
    if (focus && nodeById.get(focus)?.kind === 'project') {
      const winEdge = focusEdgeList.find((e) => e.role === 'win')
      const other = winEdge ? (winEdge.a === focus ? winEdge.b : winEdge.a) : null
      const w = other ? nodeById.get(other) : null
      if (w) {
        winRingId = w.id
        winRing = new THREE.Sprite(
          new THREE.SpriteMaterial({
            map: ringTex,
            color: 0xffc861,
            transparent: true,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
            opacity: 0.85,
          }),
        )
        winRing.position.copy(w.base)
        winRing.scale.setScalar(Math.max(3, w.scale * sizeMul[w.dk] * 2.5))
        winRing.renderOrder = 5
        world.add(winRing)
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
    labelDirty = true
  }

  /* ---- 标签排布：视野范围 + 8 向锚位 + 贪心避让 + 滞回 ---- */
  const ndc = new THREE.Vector3()
  type Cand = { n: NodeLive; sx: number; sy: number; r: number; dist: number }

  function declutter() {
    const W = el.clientWidth
    const H = el.clientHeight
    if (!W || !H) return
    const r0 = el.getBoundingClientRect()
    const cols = Math.ceil(W / CELL)
    const rows = Math.ceil(H / CELL)
    /** gLab = 已有文字/界面占位（硬约束）；gStar = 光球占位（软约束，放不下时可压） */
    const gLab = new Uint8Array(cols * rows)
    const gStar = new Uint8Array(cols * rows)
    const mark = (g: Uint8Array, x: number, y: number, w: number, h: number) => {
      const c0 = Math.max(0, Math.floor(x / CELL))
      const c1 = Math.min(cols - 1, Math.ceil((x + w) / CELL))
      const rA = Math.max(0, Math.floor(y / CELL))
      const rB = Math.min(rows - 1, Math.ceil((y + h) / CELL))
      for (let ry = rA; ry <= rB; ry++) for (let cx = c0; cx <= c1; cx++) g[ry * cols + cx] = 1
    }
    const free = (g: Uint8Array, x: number, y: number, w: number, h: number) => {
      if (x < 2 || y < 2 || x + w > W - 2 || y + h > H - 2) return false
      const c0 = Math.floor(x / CELL)
      const c1 = Math.ceil((x + w) / CELL)
      const rA = Math.floor(y / CELL)
      const rB = Math.ceil((y + h) / CELL)
      for (let ry = rA; ry <= rB; ry++) for (let cx = c0; cx <= c1; cx++) if (g[ry * cols + cx] === 1) return false
      return true
    }

    // 左下角的操作提示与图例先占位：文字不许压在 UI 上
    for (const sel of ['.hint', '.legend']) {
      const u = el.parentElement?.querySelector<HTMLElement>(sel)
      if (!u) continue
      const b = u.getBoundingClientRect()
      mark(gLab, b.left - r0.left - 6, b.top - r0.top - 4, b.width + 12, b.height + 10)
    }

    // 候选：视野范围内 + 前部球冠 + 未被淡化 + 有文字
    const camDist = camera.position.distanceTo(controls.target)
    const cands: Cand[] = []
    for (const n of nodes) {
      if (n.prio >= 999) {
        n.pass = Math.max(-2, n.pass - 1)
        continue
      }
      ndc.copy(n.sprite.position).project(camera)
      if (ndc.z > 1) continue
      const sx = (ndc.x * 0.5 + 0.5) * W
      const sy = (-ndc.y * 0.5 + 0.5) * H
      if (sx < 0 || sx > W || sy < 0 || sy > H) continue // 视野范围渲染：画面外不配文字
      const dist = camera.position.distanceTo(n.sprite.position)
      // 背面半球不放字（这是"视野范围渲染"的核心）；但你聚焦的那一小片例外，
      // 否则邻域刚好落在球背面时，唯一该有名字的节点反而全被过滤掉。
      if (dist > camDist + graphRadius * 0.45 && !(n.mustShow || relatedIds.has(n.id))) continue
      // sprite.scale 是光球边长，屏幕半径要再除 2
      const r = Math.max(4, (n.scale * sizeMul[n.dk] * H) / (4 * Math.tan((42 * Math.PI) / 360) * dist))
      cands.push({ n, sx, sy, r, dist })
      if (r >= STAR_OBSTACLE_PX) mark(gStar, sx - r, sy - r, r * 2, r * 2)
    }
    cands.sort((a, b) => a.n.prio - b.n.prio || b.n.scale - a.n.scale)

    const budget = W * H * LABEL_INK_BUDGET
    const boxOf = (c: Cand) => {
      const lines = wrapTwo(c.n.text)
      const w = Math.max(...lines.map((l) => l.length)) * 13 + 8
      const h = lines.length * 15 + 4
      return { lines, w, h }
    }
    const at = (c: Cand, i: number, w: number, h: number) => {
      const a = ANCHORS[i]!
      const gap = c.r + 5
      return {
        x: a.sx > 0 ? c.sx + gap : a.sx < 0 ? c.sx - gap - w : c.sx - w / 2,
        y: a.sy > 0 ? c.sy + gap : a.sy < 0 ? c.sy - gap - h : c.sy - h / 2,
      }
    }
    const orderOf = (c: Cand) =>
      ANCHORS.map((a, ix) => ({ ix, s: a.sx * (c.sx - W / 2) + a.sy * (c.sy - H / 2) })).sort((p, q) => q.s - p.s)

    let ink = 0
    const ok = new Set<NodeLive>()
    const tryPlace = (c: Cand, ignoreStar: boolean) => {
      const { w, h } = boxOf(c)
      if (ink + w * h > budget && !c.n.mustShow) return false
      for (const { ix } of orderOf(c)) {
        const p = at(c, ix, w, h)
        if (free(gLab, p.x, p.y, w, h) && (ignoreStar || free(gStar, p.x, p.y, w, h))) {
          mark(gLab, p.x, p.y, w, h)
          ink += w * h
          c.n.dir = ix
          c.n.gapPx = c.r + 5
          c.n.dist = c.dist
          c.n.pass = Math.min(2, c.n.pass + 1)
          ok.add(c.n)
          return true
        }
      }
      return false
    }
    // 第一轮严格避开光球；第二轮允许文字压在光晕上（有底板仍可读），
    // 否则聚焦态中心那团强光会把所有名字挤掉（实测只剩 3 条）。
    const left: Cand[] = []
    for (const c of cands) if (!tryPlace(c, false)) left.push(c)
    for (const c of left) void tryPlace(c, true)
    // 第三轮：答案集（榜单前 5）无条件落位——宁可压字，也不能让"查到的那几家"没名字
    for (const c of left) {
      if (ok.has(c.n) || !c.n.mustShow) continue
      const { w, h } = boxOf(c)
      const p2 = at(c, orderOf(c)[0]!.ix, w, h)
      mark(gLab, p2.x, p2.y, w, h)
      ink += w * h
      c.n.dir = orderOf(c)[0]!.ix
      c.n.gapPx = c.r + 5
      c.n.dist = c.dist
      c.n.pass = 2
      ok.add(c.n)
    }
    for (const c of left) if (!ok.has(c.n)) c.n.pass = Math.max(-2, c.n.pass - 1)
    for (const c of cands) if (!ok.has(c.n)) c.n.pass = Math.max(-2, c.n.pass - 1)

    // 聚焦邻域的角色标签最后占位：场景视图里"答案的名字"优先于边上的角色小字
    const ROLE_PRIO: Record<string, number> = { win: 0, buy: 1, supply: 2, bid: 3 }
    const tagCands = focusLines
      .map((fl) => {
        const na = nodeById.get(fl.a)
        const nb = nodeById.get(fl.b)
        pointOn(na?.sprite.position ?? new THREE.Vector3(), nb?.sprite.position ?? new THREE.Vector3(), 0.5, fl.a, fl.b, scratch)
        ndc.copy(scratch).project(camera)
        return { fl, x: (ndc.x * 0.5 + 0.5) * W, y: (-ndc.y * 0.5 + 0.5) * H, p: ROLE_PRIO[fl.role] ?? 9 }
      })
      .filter((t) => t.x >= 0 && t.x <= W && t.y >= 0 && t.y <= H)
      .sort((a, b) => a.p - b.p)
    for (const t of tagCands) {
      const w = (t.fl.tagObj.element.textContent ?? '').length * 11 + 8
      t.fl.tagObj.visible = free(gLab, t.x - w / 2, t.y - 8, w, 16)
      if (t.fl.tagObj.visible) mark(gLab, t.x - w / 2, t.y - 8, w, 16)
    }

    // 写 DOM：两行内容、锚位（center+margin 必须与碰撞框一致）、随距离的字号
    for (const n of nodes) {
      const on = ok.has(n) && (n.pass >= 2 || !n.everShown)
      if (on) n.everShown = true
      if (!on) {
        if (n.shown) {
          n.shown = false
          n.labelObj.visible = false
        }
        continue
      }
      n.shown = true
      n.labelObj.visible = true
      const lines = wrapTwo(n.text)
      const kids = n.labelEl.children
      if (kids[0]!.textContent !== lines[0]) kids[0]!.textContent = lines[0] ?? ''
      if (kids[1]!.textContent !== lines[1]) kids[1]!.textContent = lines[1] ?? ''
      kids[1]!.style.display = lines[1] ? 'block' : 'none'
      const a = ANCHORS[n.dir] ?? ANCHORS[0]!
      const fs = THREE.MathUtils.clamp(
        FONT_NEAR - ((n.dist - (camDist - graphRadius)) / (graphRadius * 2)) * (FONT_NEAR - FONT_FAR),
        FONT_FAR,
        FONT_NEAR,
      )
      n.labelEl.style.fontSize = fs.toFixed(1) + 'px'
      n.labelObj.center.set(a.sx > 0 ? 0 : a.sx < 0 ? 1 : 0.5, a.sy > 0 ? 0 : a.sy < 0 ? 1 : 0.5)
      n.labelEl.style.marginLeft = (a.sx > 0 ? n.gapPx : a.sx < 0 ? -n.gapPx : 0) + 'px'
      n.labelEl.style.marginTop = (a.sy > 0 ? n.gapPx : a.sy < 0 ? -n.gapPx : 0) + 'px'
      n.labelEl.dataset.dir = String(n.dir)
    }
    emit('labels', ok.size, cands.length)
    // 有节点卡在 pass===1（曾被隐藏、正在等滞回）→ 下一拍继续收敛，否则永远出不来
    for (const n of nodes) if (n.pass === 1) {
      labelDirty = true
      break
    }
  }

  /* ---- 交互（raycast 节流） ---- */
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  let downX = 0
  let downY = 0
  let lastCast = 0
  let dragging = false

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
    lastInteract = performance.now()
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
    const id = hit ? (hit.object.userData.id as string) : null
    const rect = el.getBoundingClientRect()
    emit('hover', id, id ? { x: e.clientX - rect.left, y: e.clientY - rect.top } : null)
  }
  renderer.domElement.addEventListener('pointerdown', onDown)
  renderer.domElement.addEventListener('pointerup', onUp)
  renderer.domElement.addEventListener('pointermove', onMove)
  controls.addEventListener('start', () => {
    dragging = true
    camTween = null
    renderer.domElement.style.cursor = 'grabbing'
    lastInteract = performance.now()
  })
  controls.addEventListener('end', () => {
    dragging = false
    renderer.domElement.style.cursor = 'grab'
    lastInteract = performance.now()
    labelDirty = true
  })
  controls.addEventListener('change', () => {
    labelDirty = true
  })

  function resize() {
    const w = el.clientWidth
    const h = el.clientHeight
    if (!w || !h) return
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h, false)
    labels.setSize(w, h)
    labelDirty = true
  }
  resize()
  const ro = new ResizeObserver(resize)
  ro.observe(el)

  /* ---- 帧循环 ---- */
  let raf = 0
  let lastT = performance.now()
  let time = 0
  const endA = new THREE.Vector3()
  const endB = new THREE.Vector3()

  function frameLoop() {
    raf = requestAnimationFrame(frameLoop)
    const now = performance.now()
    const dt = Math.min(0.05, (now - lastT) / 1000)
    lastT = now
    time += reduce ? 0 : dt

    // 取景补间
    if (camTween) {
      camTween.t = Math.min(1, camTween.t + dt / 0.5)
      const k = 1 - Math.pow(1 - camTween.t, 3)
      camera.position.lerpVectors(camTween.fromP, camTween.toP, k)
      controls.target.lerpVectors(camTween.fromT, camTween.toT, k)
      if (camTween.t >= 1) camTween = null
      labelDirty = true
    } else if (!reduce && !dragging && !props.focusId && now - lastInteract > 3000) {
      // 待机慢转：只作为深度线索，交互即停
      const off = camera.position.clone().sub(controls.target)
      const ang = 0.02 * dt
      const nx = off.x * Math.cos(ang) - off.z * Math.sin(ang)
      const nz = off.x * Math.sin(ang) + off.z * Math.cos(ang)
      camera.position.set(controls.target.x + nx, camera.position.y, controls.target.z + nz)
    }
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
      const shown = node.scale * sizeMul[node.dk]
      node.sprite.scale.setScalar(shown * breath)
      node.haze.scale.setScalar(node.scale * hazeMul[node.kind] * breath)
      ;(node.sprite.material as THREE.SpriteMaterial).opacity = innerOp[node.dk] * node.opacity
      ;(node.haze.material as THREE.SpriteMaterial).opacity = hazeOp[node.dk] * node.opacity
      node.pick.visible = node.opacity > 0.32
      const lop = Math.max(0, node.opacity)
      if (Math.abs(lop - node.labelOp) > 0.01) {
        node.labelEl.style.opacity = String(lop)
        node.labelOp = lop
      }
    }

    // 雾分组呼吸：错相正弦 ^4，任意时刻只有 1–2 个天区亮起；聚焦时整体压暗
    for (const g of mistGroups) {
      const b = reduce ? 0.1 : 0.02 + 0.38 * Math.pow(0.5 + 0.5 * Math.sin(time * 0.785 + g.phase), 4)
      const targetOp = props.focusId ? 0.015 : b
      g.op += (targetOp - g.op) * (1 - Math.exp(-dt * 2.2))
      g.mat.opacity = g.op
    }
    if (backbone) {
      const want = props.focusId ? BACKBONE_OP * 0.25 : BACKBONE_OP
      backbone.mat.opacity += (want - backbone.mat.opacity) * (1 - Math.exp(-dt * 2.2))
    }

    // 中标常驻环跟着星体走
    if (winRing) {
      const t = nodeById.get(winRingId)
      if (t) winRing.position.copy(t.sprite.position)
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
      const tagEl = fl.tagObj.element as HTMLDivElement
      tagEl.style.opacity = String(Math.min(na.opacity, nb.opacity) * 2.2)
      tagEl.style.color =
        fl.role === 'win' ? 'rgba(255, 200, 97, 1)' : fl.role === 'buy' ? 'rgba(121, 208, 255, 0.9)' : fl.role === 'supply' ? 'rgba(201, 138, 75, 0.85)' : 'rgba(142, 166, 196, 0.45)'
      tagEl.classList.toggle('strong', fl.role === 'win')
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

    // 标签排布：拖拽中冻结，停手 120ms 后重算一次（避免旋转时文字疯狂闪跳）
    if (labelDirty && !dragging && now - lastDeclutter > 120 && !camTween) {
      lastDeclutter = now
      labelDirty = false
      declutter()
    }

    if (pendingReadyAt) {
      emit('ready', Math.round(performance.now() - pendingReadyAt))
      pendingReadyAt = 0
    }
  }
  raf = requestAnimationFrame(frameLoop)

  /* ---- 数据与强调态监听 ---- */
  watch(() => [props.nodes, props.edges, props.layoutMode], () => void rebuild(), { deep: false })
  watch(
    () => [props.focusId, props.highlightIds, props.ranks, props.dimOthers, props.hiddenKinds],
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
    coreTex.dispose()
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
