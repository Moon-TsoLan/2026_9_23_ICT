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
import { SLOT_CSS, SLOT_HEX } from '@/constants/slots'
import { hash, homeEye, placeStars, visualScale } from '@/composables/useGraphLayout'
import type { DisplayKind, GraphEdge, GraphNode, NodeKind } from '@/types/graph'
import { DISPLAY_KIND, EDGE_ROLE_LABEL } from '@/types/graph'
import { disambiguate, rankMark, wrapTwo } from '@/utils/label'

const props = withDefaults(
  defineProps<{
    nodes: GraphNode[]
    edges: GraphEdge[]
    focusId?: string | null
    highlightIds?: string[]
    /** 场景答案涉及的节点：只提亮，不替换底图 */
    sceneIds?: string[]
    ranks?: Record<string, number>
    dimOthers?: boolean
    /** 图例过滤：被关掉的类别不画也不标注 */
    hiddenKinds?: DisplayKind[]
    /** 当前场景的答案边：独立成层并按 role 上色，让"切场景"在视觉上真的换了问题。
     *  这些边不再混进底图雾层，否则五个场景看上去一模一样。 */
    sceneEdges?: GraphEdge[]
    /** 排布模式：disc = 原来的扁平圆盘，sphere = 球面星团（画面效果由使用者确认） */
    layoutMode?: LayoutMode
    /**
     * 显示模式（由状态机决定，不再从"有没有 focus"去猜）：
     *  overview 总览：全亮
     *  picked   S0 选中：只亮选中点本身，邻居也暗 —— S0 不回答关系问题
     *  scene    场景态：主体 + 答案集亮，其余暗
     *  pool     多选态：供应商池亮（可选对象），其余暗
     */
    displayMode?: 'overview' | 'picked' | 'scene' | 'pool' | 's6'
    /** 场景态锁定：非答案节点不可点，悬停仍可看全名 */
    locked?: boolean
    /** 对比主体（S4/S5）的节点 id，顺序即配色顺序 */
    subjectIds?: string[]
  }>(),
  {
    focusId: null,
    highlightIds: () => [],
    sceneIds: () => [],
    ranks: () => ({}),
    dimOthers: true,
    hiddenKinds: () => [],
    sceneEdges: () => [],
    layoutMode: 'sphere',
    displayMode: 'overview',
    locked: false,
    subjectIds: () => [],
  },
)

const emit = defineEmits<{
  (e: 'select', id: string | null): void
  /** Shift+点：把这个节点交给"对比篮"（S4/S5 需要多主体） */
  (e: 'basket', id: string): void
  /** 场景态锁定下试图点别的节点 */
  (e: 'blocked', id: string): void
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

/** 中标供应商的描边色：多选态的"可选池"里不点也能看出谁能进对比篮 */
const WIN_GOLD = 0xffc861

/**
 * 流光带开关。
 * 关掉的原因不是它不好看，而是它与曲线层**各用一套配色**（曲线在对比态按来源主体上色，
 * 带子永远按关系类型上色），同一批边叠两层，谁盖过谁取决于镜头远近——
 * 边越少框得越近，带子就越粗，于是出现"色点是主体色、线却是金光"。
 * 先摘掉它把语义收敛成一套；等系统完整后再作为"流量层"加回来，届时必须与曲线共用配色。
 */
const SHOW_FLOW_BANDS = false

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
  /** 滞回计数：连续两次放得下才允许出现（0 或 1 表示还没站稳），失败立即归零 */
  pass: number
  shown: boolean
  dir: number
  gapPx: number
  dist: number
  /** 是否曾经显示过：没显示过的第一次直接给，避免滞回把首屏卡死 */
  everShown: boolean
}

type Pulse = { sprite: THREE.Sprite; t: number; size: number }


type MistGroup = {
  points: THREE.Points
  mat: THREE.PointsMaterial
  phase: number
  op: number
}

let cleanup = () => {}

/** 取景/定位的真实实现在 onMounted 里（需要 camera 与节点表），这里做转接 */
let frameFn: (animated?: boolean) => void = () => {}
let frameToFn: (ids: string[]) => void = () => {}
let flyToFn: (id: string) => void = () => {}
let hasNodeFn: (id: string) => boolean = () => false

defineExpose({
  frame: (animated?: boolean) => frameFn(animated),
  flyTo: (id: string) => flyToFn(id),
  hasNode: (id: string) => hasNodeFn(id),
  /** 框住一组节点（场景答案集） */
  frameTo: (ids: string[]) => frameToFn(ids),
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
  /** 场景答案边层：按 role（对比态按来源主体）上色的唯一一条边层，带角色标签。
   *  由 applyEmphasis 驱动（轻量，只重建这几十条线），**不放进 rebuild** ——
   *  否则每次切场景都会走一遍全量力导重排，用户看到"图重新渲染"。 */
  let sceneLines: Array<{ line: THREE.Line; tagObj: CSS2DObject; a: string; b: string; role: string }> = []
  /** 聚焦用的角色配色，场景边层复用同一套语义色 */
  const ROLE_COLOR: Record<string, { c: number; o: number }> = {
    win: { c: 0xffc861, o: 0.72 }, // 中标：金
    buy: { c: 0x79d0ff, o: 0.5 }, // 采购：冰蓝
    supply: { c: 0xc98a4b, o: 0.46 }, // 供货：铜
    bid: { c: 0x8ea6c4, o: 0.3 }, // 投标：冷灰
  }

  function clearSceneLines() {
    for (const it of sceneLines) {
      world.remove(it.line, it.tagObj)
      it.line.geometry.dispose()
      ;(it.line.material as THREE.Material).dispose()
    }
    sceneLines = []
  }
  /** 复用式环池：进出多选态不反复创建销毁对象 */
  const emblems: Array<{ sprite: THREE.Sprite; id: string; base: number }> = []
  const slotIndex = new Map<string, number>()

  /** 这条边属于第几个对比主体（-1 = 不属于任何主体，按角色上色） */
  function slotOf(a: string, b: string) {
    return slotIndex.get(a) ?? slotIndex.get(b) ?? -1
  }

  function setEmblems(list: Array<{ id: string; color: number; radius: number }>) {
    for (let i = 0; i < list.length; i++) {
      const spec = list[i]!
      let e = emblems[i]
      if (!e) {
        const sprite = new THREE.Sprite(
          new THREE.SpriteMaterial({
            map: ringTex,
            transparent: true,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
            opacity: 0.55, // 环是标记不是主角：太亮会在密集区连成"锁子甲"
          }),
        )
        sprite.renderOrder = 5
        world.add(sprite)
        e = { sprite, id: '', base: 1 }
        emblems[i] = e
      }
      e.id = spec.id
      e.base = spec.radius
      const m = e.sprite.material as THREE.SpriteMaterial
      m.color.setHex(spec.color)
      m.opacity = spec.soft ? 0.4 : 0.62
      e.sprite.visible = true
      const n = nodeById.get(spec.id)
      if (n) {
        e.sprite.position.copy(n.sprite.position)
        e.sprite.scale.setScalar(spec.radius)
      }
    }
    for (let i = list.length; i < emblems.length; i++) emblems[i]!.sprite.visible = false
  }
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
  /** 布局 scale 基准（底图节点数）。见 rebuild() 中的说明：不能跟随场景节点数变化。 */
  let graphScaleBase: number | null = null
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
    controls.minDistance = graphRadius * 0.55 // 恢复全图下限（frameToIds 会临时调小）
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

  /** 把镜头交给一组节点（场景答案集）：只框住它们，背景仍在画面里，但不抢注意力 */
  function frameToIds(ids: string[]) {
    const picked = ids.map((i) => nodeById.get(i)).filter(Boolean) as NodeLive[]
    if (!picked.length) return frame(true)
    const c = new THREE.Vector3()
    for (const p of picked) c.add(p.base)
    c.multiplyScalar(1 / picked.length)
    let r = 8
    for (const p of picked) r = Math.max(r, p.base.distanceTo(c) + p.scale * sizeMul[p.dk])
    // 上下限都夹一下：太近会只剩答案看不见宇宙，太远又回到原来那种"一小坨"
    const dist = THREE.MathUtils.clamp(r * 2.1, graphRadius * 0.2, graphRadius * 1.05)
    controls.minDistance = Math.max(6, r * 0.5)
    controls.maxDistance = Math.max(dist * 2.4, graphRadius * 4.2)
    camera.far = Math.max(2000, graphRadius * 8)
    camera.updateProjectionMatrix()
    const dir = camera.position.clone().sub(controls.target).normalize()
    const pos = c.clone().add(dir.multiplyScalar(dist))
    if (reduce) {
      controls.target.copy(c)
      camera.position.copy(pos)
      camTween = null
      labelDirty = true
      return
    }
    camTween = { fromP: camera.position.clone(), toP: pos, fromT: controls.target.clone(), toT: c, t: 0 }
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
  frameToFn = frameToIds

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
    clearEmblems()
    clearSceneLines()
    for (const p of pulses) {
      world.remove(p.sprite)
      ;(p.sprite.material as THREE.Material).dispose()
    }
    pulses.length = 0
    for (const d of disposables) d.dispose()
    disposables.length = 0
  }

  /** 环池整体销毁（只在重建节点表时调用；平时靠 setEmblems 复用） */
  function clearEmblems() {
    for (const e of emblems) {
      world.remove(e.sprite)
      ;(e.sprite.material as THREE.Material).dispose()
    }
    emblems.length = 0
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

    // scale 基准固定为"底图规模"，不随场景层增删变化。
    // 场景层只是在底图上**追加**节点，若基准跟着总数走，多几个节点就会让整张图
    // 重新缩放（数据层一个点没动、渲染层整体位移，即用户看到的"图抖了一下"）。
    // 判定底图换了的依据：本次节点数不比基准多 —— 说明场景层被清掉或底图重采样了。
    if (graphScaleBase === null || props.nodes.length < graphScaleBase) {
      graphScaleBase = props.nodes.length
    }
    const placed = placeStars(props.nodes, props.edges, props.layoutMode, graphScaleBase)
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
       与旧版的差别只有两点：分组按天区（见 sectorOf）、外加一层不参与呼吸的常驻骨架。
       注意：场景答案边**不参与**这一层，它们由下面独立的 sceneLines 层按 role 绘制。 */
    const edgeKey = (e: GraphEdge) => e.a + '|' + e.b + '|' + e.role
    const sceneEdgeKeys = new Set(props.sceneEdges.map(edgeKey))
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
        if (sceneEdgeKeys.has(edgeKey(e))) continue // 场景边走独立层，避免重复绘制
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
  let litIds = new Set<string>()
  let focusEdgeList: GraphEdge[] = []
  /** 流光带只跟着"连着主体的边"，否则整张答案图的粒子会爆带容量 */
  let bandEdgeList: GraphEdge[] = []

  function applyEmphasis() {
    const focus = props.focusId && nodeById.has(props.focusId) ? props.focusId : null
    const showRelations = props.displayMode === 'scene' || props.displayMode === 'compared' || props.displayMode === 's6'
    const highlights = new Set(props.highlightIds.filter((id) => nodeById.has(id)))
    const inScene = new Set(props.sceneIds.filter((id) => nodeById.has(id)))
    const hidden = new Set<DisplayKind>(props.hiddenKinds)

    relatedIds = new Set<string>()
    focusEdgeList = []
    bandEdgeList = []
    // 只有"已经提问"的状态才产生关系数据（scene / compared / s6）。
    // picked、pool、overview 一律不算底图 1.5 跳 —— 上一版在这里算了却只挡住曲线层，
    // 于是流光带和金环从侧门漏出去，表现为"选中节点周围自己亮了"、"进多选态线不熄"。
    // 场景态也绝不回退底图 1.5 跳：一家供应商在底图里能牵出几百条线（实测 S3 360 条）。
    if (showRelations) {
      // 哪怕一条边都没有（交集为空），也绝不退回"底图 1.5 跳"——
      // 那会把第一家自己的历史关系错画成答案，看起来像"亮了别的节点"。
      for (const id of props.sceneIds) if (nodeById.has(id)) relatedIds.add(id)
      // 每个对比主体都算"焦点"，否则第二家起的边没有标签
      const anchors = new Set<string>(props.subjectIds.length ? props.subjectIds : focus ? [focus] : [])
      for (const e of props.sceneEdges) {
        if (!nodeById.has(e.a) || !nodeById.has(e.b)) continue
        relatedIds.add(e.a)
        relatedIds.add(e.b)
        // 整张答案图由 sceneLines 层按颜色画；这里只留"连着主体的那一跳"带曲线+角色标签。
        // 同一条边被两层各画一遍（一层加色一层不加）就是"线条样式不一致"的来源。
        if (!anchors.size || anchors.has(e.a) || anchors.has(e.b)) {
          focusEdgeList.push(e)
          bandEdgeList.push(e)
        }
      }
    }
    for (const id of highlights) relatedIds.add(id)
    for (const id of inScene) relatedIds.add(id)

    // 亮度完全由 displayMode 决定
    const mode = props.displayMode
    for (const n of nodes) {
      if (hidden.has(n.dk)) {
        n.target = 0
        continue
      }
      if (mode === 'overview') n.target = 1
      else if (mode === 'picked') n.target = n.id === focus ? 1 : 0.14
      else if (mode === 'pool') n.target = n.dk === 'supplier' ? 1 : 0.16
      // s6 / scene / compared：主体与答案亮，其余暗
      else n.target = relatedIds.has(n.id) ? 1 : 0.18
    }
    litIds =
      mode === 'overview'
        ? new Set(nodes.map((n) => n.id))
        : mode === 'picked'
          ? new Set(focus ? [focus] : [])
          : mode === 'pool'
            ? new Set(nodes.filter((n) => n.dk === 'supplier').map((n) => n.id))
            : relatedIds

    // 标签文字与优先级：答案集(榜单) > 聚焦邻域 > 高亮 > 采购方 > 中标 > 供应商 > 投标 > 项目
    const KIND_PRIO: Record<DisplayKind, number> = { buyer: 20, supplier: 24, project: 40 }
    const weightOf = new Map(props.nodes.map((n) => [n.id, n.weight]))
    const kindOf = new Map(props.nodes.map((n) => [n.id, n.kind]))
    const ambientMode = props.displayMode === 'overview' && nodes.length > OVERVIEW_MIN_KIND
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
      else if (ambientMode && DISPLAY_KIND[kind] !== 'buyer') prio = 999 // 总览=氛围层：只给采购单位的星团命名，其余靠悬停
      n.prio = prio
      n.mustShow = !!rank || (!!focus && n.id === focus)
      if (!n.text || hidden.has(DISPLAY_KIND[kind]) || (n.target < 0.5 && !n.mustShow)) n.prio = 999
    }



    // 场景答案边 → 独立曲线层：按 role 上色，让五个场景在视觉上真的不同。
    // 后端为每个场景产出的边角色本就不同（S1 采购/中标/供货、S3 中标/投标、
    // S4 供应商→采购单位、S5 采购/中标），此前它们被无差别倒进雾层，
    // 所以"切场景看起来没换问题"。这里逐条重建（数量仅几十条，代价可忽略）。
    /**
     * 只有"多家对比"才按来源主体着色。单主体场景（S1/S2/S3）里"这条线是谁的"
     * 由聚光灯本身已经回答，颜色必须继续表示关系类型，否则会出现同一条边
     * 线是主体橙、标签却写蓝色"采购"的自相矛盾。
     * 必须在画边之前填好——上一版填在画边之后，等于永远用的是上一次的表。
     */
    slotIndex.clear()
    if (props.displayMode === 'compared') props.subjectIds.forEach((id, i) => slotIndex.set(id, i))

    clearSceneLines()
    if (props.sceneEdges.length) {
      for (const e of props.sceneEdges) {
        const na = nodeById.get(e.a)
        const nb = nodeById.get(e.b)
        if (!na || !nb) continue
        const slot = slotOf(e.a, e.b)
        const look = slot >= 0 ? { c: SLOT_HEX[slot % SLOT_HEX.length], o: 0.8 } : ROLE_COLOR[e.role] ?? ROLE_COLOR.bid!
        const geo = new THREE.BufferGeometry()
        const arr = new Float32Array((SEG + 1) * 3)
        for (let i = 0; i <= SEG; i++) {
          pointOn(na.base, nb.base, i / SEG, e.a, e.b, scratch)
          arr[i * 3] = scratch.x
          arr[i * 3 + 1] = scratch.y
          arr[i * 3 + 2] = scratch.z
        }
        geo.setAttribute('position', new THREE.BufferAttribute(arr, 3))
        const line = new THREE.Line(
          geo,
          new THREE.LineBasicMaterial({
            color: look.c,
            transparent: true,
            opacity: look.o,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
          }),
        )
        line.frustumCulled = false
        line.renderOrder = 2
        world.add(line)
        const tag = document.createElement('div')
        tag.className = 'star-edge'
        tag.textContent = EDGE_ROLE_LABEL[e.role] ?? ''
        const tagObj = new CSS2DObject(tag)
        tagObj.position.copy(na.base)
        world.add(tagObj)
        sceneLines.push({ line, tagObj, a: e.a, b: e.b, role: e.role })
      }
    }

    // 环：多选态 = 中标供应商金环（不点也能看出谁能进对比篮）；
    //     对比态 = 各主体的 slot 色环，与它的边、矩阵列头同色。
    const emblemSpecs: Array<{ id: string; color: number; radius: number; soft?: boolean }> = []
    if (props.displayMode === 'pool') {
      for (const n of nodes) {
        if (n.kind !== 'winner') continue
        emblemSpecs.push({ id: n.id, color: WIN_GOLD, radius: Math.max(2.6, n.scale * sizeMul[n.dk] * 1.55), soft: true })
      }
    } else if (props.displayMode === 'compared') {
      for (const id of props.subjectIds) {
        const n = nodeById.get(id)
        if (!n) continue
        emblemSpecs.push({ id, color: SLOT_HEX[slotIndex.get(id) ?? 0], radius: Math.max(3.4, n.scale * sizeMul[n.dk] * 2.1) })
      }
    }
    setEmblems(emblemSpecs)

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

    // 左下角的操作提示、图例、以及顶部提示条先占位：文字不许压在 UI 上
    for (const sel of ['.hint', '.legend', '.notice', '.lockbar']) {
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
        n.pass = 0
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

    /**
     * 角色标签（中标/采购/供应/投标）按角色分批占位。
     * 它们只有两个字，却是"场景到底在说什么"的载体，所以插在
     * "答案的名字"与"其余名字"之间，而不是排到最后被挤光（实测会被挤到 0 条）。
     */
    const ROLE_ORDER: Record<string, number> = { win: 0, buy: 1, supply: 2, bid: 3 }
    function placeTags(roles: string[]) {
      const list = sceneLines
        .filter((fl) => roles.includes(fl.role))
        .map((fl) => {
          const na = nodeById.get(fl.a)
          const nb = nodeById.get(fl.b)
          pointOn(na?.sprite.position ?? new THREE.Vector3(), nb?.sprite.position ?? new THREE.Vector3(), 0.5, fl.a, fl.b, scratch)
          ndc.copy(scratch).project(camera)
          return { fl, x: (ndc.x * 0.5 + 0.5) * W, y: (-ndc.y * 0.5 + 0.5) * H }
        })
        .filter((t) => t.x >= 0 && t.x <= W && t.y >= 0 && t.y <= H)
        .sort((a, b) => (ROLE_ORDER[a.fl.role] ?? 9) - (ROLE_ORDER[b.fl.role] ?? 9))
      for (const t of list) {
        const w = (t.fl.tagObj.element.textContent ?? '').length * 11 + 8
        t.fl.tagObj.visible = free(gLab, t.x - w / 2, t.y - 8, w, 16)
        if (t.fl.tagObj.visible) mark(gLab, t.x - w / 2, t.y - 8, w, 16)
      }
    }

    // 第一轮严格避开光球；第二轮允许文字压在光晕上（有底板仍可读），
    // 否则聚焦态中心那团强光会把所有名字挤掉（实测只剩 3 条）。
    const left: Cand[] = []
    // 顺序：答案与主体的名字 → 中标/采购标签 → 其余名字 → 供应/投标标签
    for (const c of cands) if (c.n.mustShow && !tryPlace(c, false)) left.push(c)
    placeTags(['win', 'buy'])
    for (const c of cands) if (!c.n.mustShow && !tryPlace(c, false)) left.push(c)
    placeTags(['supply', 'bid'])
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
    for (const c of left) if (!ok.has(c.n)) c.n.pass = 0
    for (const c of cands) if (!ok.has(c.n)) c.n.pass = 0


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
      // 对比主体：标签前挂一个与边、环、矩阵列头同色的点
      const slot = slotIndex.get(n.id)
      if (slot === undefined) {
        if (n.labelEl.dataset.slot !== undefined) delete n.labelEl.dataset.slot
      } else {
        n.labelEl.dataset.slot = String(slot)
        n.labelEl.style.setProperty('--slot', SLOT_CSS[slot % SLOT_CSS.length]!)
      }
    }
    const f = nodeById.get(props.focusId ?? '')
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

  /** 屏幕空间近邻的兜底容差（px）。
   *  为什么需要：拾取球只有视觉光球的约 0.62 倍，而光球纹理是长尾衰减——
   *  用户按"看起来的大小"去点，落点经常在"有颜色但没有拾取体"的光晕区，
   *  加上星体每帧漂移（±0.1~0.2 世界单位），悬停看到的点与点击时的点可能已不在同一处。
   *  实测同一组 10 个固定格点 Shift+点多选只成功 1 次。 */
  const PICK_TOLERANCE_PX = 14

  const _pv = new THREE.Vector3()

  /** 射线未命中时：在指针周围找屏幕投影最近的节点，避免"差几像素就点不中" */
  function nearestByScreen(e: PointerEvent, rect: DOMRect, factor: number) {
    const H = rect.height
    const localX = e.clientX - rect.left
    const localY = e.clientY - rect.top
    const tol = PICK_TOLERANCE_PX * factor
    const camDist = camera.position.distanceTo(controls.target)
    let best: { id: string; d2: number } | null = null
    for (const n of nodes) {
      if (n.opacity <= 0.32) continue // 与 pick.visible 的阈值保持一致
      _pv.copy(n.sprite.position).project(camera)
      if (_pv.z > 1) continue
      const sx = (_pv.x * 0.5 + 0.5) * rect.width
      const sy = (-_pv.y * 0.5 + 0.5) * H
      // 背面半球不参与兜底，否则聚焦邻域落在球背面时会被抢走
      const dist = camera.position.distanceTo(n.sprite.position)
      if (dist > camDist + graphRadius * 0.45 && !n.mustShow && !relatedIds.has(n.id)) continue
      const r = Math.max(4, (n.scale * sizeMul[n.dk] * H) / (4 * Math.tan((42 * Math.PI) / 360) * dist))
      const dx = sx - localX
      const dy = sy - localY
      const d2 = dx * dx + dy * dy
      const hitR = Math.max(tol, r * 0.85)
      if (d2 > hitR * hitR) continue
      if (!best || d2 < best.d2) best = { id: n.id, d2 }
    }
    return best?.id ?? null
  }

  function cast(e: PointerEvent) {
    const rect = renderer.domElement.getBoundingClientRect()
    pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
    pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    raycaster.setFromCamera(pointer, camera)
    const hit = raycaster.intersectObjects(picks, false).find((h) => h.object.visible)
    if (hit) return { id: hit.object.userData.id as string, viaFallback: false }
    const near = nearestByScreen(e, rect, 1)
    return near ? { id: near, viaFallback: true } : null
  }

  function onDown(e: PointerEvent) {
    downX = e.clientX
    downY = e.clientY
    lastInteract = performance.now()
  }
  function onUp(e: PointerEvent) {
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 6) return
    const id = cast(e)?.id ?? null
    // 场景态锁定：点别的星无效（要换对象必须先退出），但点空白仍然是"退出这个问题"
    if (props.locked) {
      if (!id) emit('select', null)
      else if (!litIds.has(id)) emit('blocked', id)
      return
    }
    if (id && e.shiftKey) {
      emit('basket', id)
      return
    }
    emit('select', id)
  }
  function onMove(e: PointerEvent) {
    if (e.buttons) return
    const now = performance.now()
    if (now - lastCast < 60) return
    lastCast = now
    const id = cast(e)?.id ?? null
    // "点不动"必须看得见，否则用户只会以为界面坏了
    renderer.domElement.style.cursor = props.locked && id && !litIds.has(id) ? 'not-allowed' : dragging ? 'grabbing' : 'grab'
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
    } else if (!reduce && !dragging && props.displayMode === 'overview' && now - lastInteract > 3000) {
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
      // 已提问（场景/对比）彻底安静；S0 选中与多选挑选时压到 0.45 倍但仍在呼吸，
      // 否则挑星的时候背景雾反而比星还抢眼。
      const targetOp =
        props.displayMode === 'scene' || props.displayMode === 'compared' || props.displayMode === 's6'
          ? 0.015
          : props.displayMode === 'picked' || props.displayMode === 'pool'
            ? b * 0.45
            : b
      g.op += (targetOp - g.op) * (1 - Math.exp(-dt * 2.2))
      g.mat.opacity = g.op
    }
    if (backbone) {
      const want = props.displayMode === 'scene' ? BACKBONE_OP * 0.25 : BACKBONE_OP
      backbone.mat.opacity += (want - backbone.mat.opacity) * (1 - Math.exp(-dt * 2.2))
    }

    // 中标常驻环跟着星体走
    for (const e of emblems) {
      if (!e.sprite.visible) continue
      const t = nodeById.get(e.id)
      if (t) {
        e.sprite.position.copy(t.sprite.position)
        e.sprite.scale.setScalar(e.base * (reduce ? 1 : 1 + Math.sin(time * 0.5 + t.phase) * 0.05))
      }
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

    // 唯一的边层：曲线跟着星体的轻微漂移走，标签落在弧中点
    for (const it of sceneLines) {
      const na = nodeById.get(it.a)
      const nb = nodeById.get(it.b)
      if (!na || !nb) continue
      endA.copy(na.sprite.position)
      endB.copy(nb.sprite.position)
      const attr = it.line.geometry.getAttribute('position') as THREE.BufferAttribute
      const arr = attr.array as Float32Array
      for (let i = 0; i <= SEG; i++) {
        pointOn(endA, endB, i / SEG, it.a, it.b, scratch)
        arr[i * 3] = scratch.x
        arr[i * 3 + 1] = scratch.y
        arr[i * 3 + 2] = scratch.z
      }
      attr.needsUpdate = true
      pointOn(endA, endB, 0.5, it.a, it.b, scratch)
      it.tagObj.position.copy(scratch)
      const tagEl = it.tagObj.element as HTMLDivElement
      tagEl.style.opacity = String(Math.min(na.opacity, nb.opacity) * 2.2)
      tagEl.style.color =
        it.role === 'win' ? 'rgba(255, 200, 97, 1)' : it.role === 'buy' ? 'rgba(121, 208, 255, 0.9)' : it.role === 'supply' ? 'rgba(201, 138, 75, 0.85)' : 'rgba(142, 166, 196, 0.5)'
      tagEl.classList.toggle('strong', it.role === 'win')
    }

    // 流光粒子带（见 SHOW_FLOW_BANDS：当前停用）
    const focusActive = SHOW_FLOW_BANDS && bandEdgeList.length > 0
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
    if (SHOW_FLOW_BANDS && bandFade > 0.03) {
      for (const edge of bandEdgeList) {
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
    () => [props.focusId, props.highlightIds, props.sceneIds, props.ranks, props.dimOthers, props.hiddenKinds, props.sceneEdges, props.displayMode, props.locked, props.subjectIds],
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
