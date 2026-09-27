<script setup lang="ts">
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import * as THREE from 'three'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  bodies,
  bodyOf,
  focusOf,
  hash,
  homeEye,
  idleEdges,
  kindName,
  placeStars,
  relations,
  roster,
  selectionGroups,
  selectionStats,
  selectionTitle,
  type Kind,
  type RelationMode,
  type StoryEdge,
} from '../universe'

const host = ref<HTMLDivElement | null>(null)
const mode = ref<RelationMode>('rival')
const selected = ref<string | null>(null)
const picked = computed(() => (selected.value ? (bodyOf(selected.value) ?? null) : null))
const title = computed(() => selectionTitle(selected.value, mode.value))
const stats = computed(() => selectionStats(selected.value, mode.value))
const groups = computed(() => (selected.value ? selectionGroups(selected.value, mode.value) : []))
const rosterRows = computed(() => roster(mode.value))
const modeName = computed(() => relations.find((item) => item.id === mode.value)?.name ?? '')

function pickMode(next: RelationMode) {
  if (next === mode.value) return
  mode.value = next
}

function toggle(id: string) {
  selected.value = selected.value === id ? null : id
}

let stop = () => {}

const tint: Record<Kind, string> = {
  project: '#f0d0b0',
  winner: '#fff6ec',
  bidder: '#efe4d6',
  vendor: '#f0d8bc',
  buyer: '#f4e7d6',
}
const innerOp: Record<Kind, number> = {
  project: 0.86,
  winner: 0.84,
  bidder: 0.58,
  vendor: 0.5,
  buyer: 0,
}
const hazeOp: Record<Kind, number> = {
  project: 0.2,
  winner: 0.18,
  bidder: 0.14,
  vendor: 0.16,
  buyer: 0.32,
}
const hazeMul: Record<Kind, number> = {
  project: 2.05,
  winner: 2.15,
  bidder: 2.25,
  vendor: 2.45,
  buyer: 1.02,
}

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

onMounted(() => {
  const el = host.value
  if (!el) return
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const placed = placeStars()
  const placeMap = new Map(placed.map((p) => [p.id, p]))

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

  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 500)
  let maxR = 8
  for (const p of placed) maxR = Math.max(maxR, Math.hypot(p.x, p.y, p.z))
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = !reduce
  controls.dampingFactor = 0.065
  controls.rotateSpeed = 0.4
  controls.zoomSpeed = 0.5
  controls.minDistance = maxR * 0.55
  controls.maxDistance = maxR * 4.2
  controls.target.set(maxR * 0.1, 0, 0)
  const eye = new THREE.Vector3(homeEye.x, homeEye.y, homeEye.z).normalize().multiplyScalar(maxR * 1.95)
  camera.position.copy(controls.target).add(eye)

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

  const dustCount = 380
  const dustPos = new Float32Array(dustCount * 3)
  for (let i = 0; i < dustCount; i++) {
    const theta = Math.random() * Math.PI * 2
    const phi = Math.acos(2 * Math.random() - 1)
    const radius = 16 + Math.random() * 52
    dustPos[i * 3] = radius * Math.sin(phi) * Math.cos(theta)
    dustPos[i * 3 + 1] = radius * Math.cos(phi) * 0.62
    dustPos[i * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta)
  }
  const dustGeo = new THREE.BufferGeometry()
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3))
  const dust = new THREE.Points(
    dustGeo,
    new THREE.PointsMaterial({
      map: starTex,
      size: 0.28,
      color: 0xe7d8c6,
      transparent: true,
      opacity: 0.22,
      depthWrite: false,
      sizeAttenuation: true,
    }),
  )
  dust.renderOrder = 0
  world.add(dust)

  const pickGeo = new THREE.SphereGeometry(1, 8, 8)
  const pickMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false })

  type NodeLive = {
    id: string
    kind: Kind
    base: THREE.Vector3
    phase: number
    opacity: number
    scale: number
    sprite: THREE.Sprite
    haze: THREE.Sprite
    pick: THREE.Mesh
    label: HTMLDivElement
    labelObj: CSS2DObject
  }

  const nodes: NodeLive[] = []
  const picks: THREE.Object3D[] = []
  const nodePos = new Map<string, THREE.Vector3>()

  for (const body of bodies) {
    const p = placeMap.get(body.id)!
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
    sprite.scale.setScalar(
      body.kind === 'buyer' ? p.scale * 0.4 : body.kind === 'project' ? p.scale * 1.55 : p.scale * 0.72,
    )
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
    pick.userData.alive = true
    world.add(pick)
    picks.push(pick)

    const label = document.createElement('div')
    label.className = 'star-label'
    label.textContent = body.label
    const labelObj = new CSS2DObject(label)
    labelObj.position.copy(base)
    world.add(labelObj)

    nodes.push({
      id: body.id,
      kind: body.kind,
      base,
      phase: hash(body.id) * Math.PI * 2,
      opacity: 1,
      scale: p.scale,
      sprite,
      haze,
      pick,
      label,
      labelObj,
    })
    nodePos.set(body.id, sprite.position)
  }

  const SEG = 32
  type LineLive = {
    key: string
    a: string
    b: string
    label?: string
    opacity: number
    target: number
    line: THREE.Line
    tag: HTMLDivElement
    tagObj: CSS2DObject
  }
  const lines: LineLive[] = []

  function edgeKey(e: StoryEdge) {
    return `${[e.a, e.b].sort().join('~')}::${e.label ?? ''}`
  }

  function ensureLine(e: StoryEdge) {
    const key = edgeKey(e)
    const found = lines.find((l) => l.key === key)
    if (found) return found
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array((SEG + 1) * 3), 3))
    const line = new THREE.Line(
      geo,
      new THREE.LineBasicMaterial({
        color: 0xf4e6d4,
        transparent: true,
        opacity: 0,
        depthWrite: false,
      }),
    )
    line.frustumCulled = false
    line.renderOrder = 1
    world.add(line)
    const tag = document.createElement('div')
    tag.className = 'star-edge'
    tag.textContent = e.label ?? ''
    const tagObj = new CSS2DObject(tag)
    world.add(tagObj)
    const live: LineLive = { key, a: e.a, b: e.b, label: e.label, opacity: 0, target: 0, line, tag, tagObj }
    lines.push(live)
    return live
  }

  const bandMax = 4096
  const bandTex = makeTex([
    [0, 'rgba(255,255,255,0.42)'],
    [0.18, 'rgba(255,255,255,0.18)'],
    [0.42, 'rgba(255,255,255,0.05)'],
    [1, 'rgba(255,255,255,0)'],
  ])
  function makeRibbon(size: number) {
    const pos = new Float32Array(bandMax * 3)
    const col = new Float32Array(bandMax * 3)
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3))
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
    return { pos, col, geo, points }
  }
  const winRibbon = makeRibbon(6.8)
  const bidRibbon = makeRibbon(4.6)
  let bandFade = 0

  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  let downX = 0
  let downY = 0
  let hover = ''
  const side = new THREE.Vector3()
  const up = new THREE.Vector3(0, 1, 0)
  const alt = new THREE.Vector3(1, 0, 0)
  const scratch = new THREE.Vector3()

  function pointOn(a: THREE.Vector3, b: THREE.Vector3, t: number, ida: string, idb: string, out: THREE.Vector3) {
    side.crossVectors(scratch.copy(b).sub(a), up)
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
    if (!hit) {
      selected.value = null
      return
    }
    toggle(hit.object.userData.id as string)
  }
  function onMove(e: PointerEvent) {
    if (e.buttons) return
    const hit = cast(e)
    hover = hit ? (hit.object.userData.id as string) : ''
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

  function onKey(e: KeyboardEvent) {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
    if (e.key === '1') pickMode('rival')
    if (e.key === '2') pickMode('coop')
  }
  window.addEventListener('keydown', onKey)

  const clock = new THREE.Clock()
  let raf = 0

  function frame() {
    raf = requestAnimationFrame(frame)
    const dt = Math.min(0.05, clock.getDelta())
    const time = clock.elapsedTime
    controls.update()

    const focus = selected.value ? focusOf(selected.value, mode.value) : null
    const useBands = !!focus?.bands
    const source = focus ? focus.edges : idleEdges(mode.value)
    const desired = new Map<string, StoryEdge>()
    const bandRoles = new Map<string, StoryEdge['role']>()
    if (!useBands) {
      for (const edge of source) {
        ensureLine(edge)
        desired.set(edgeKey(edge), edge)
      }
    } else {
      for (const edge of source) {
        ensureLine(edge)
        bandRoles.set(edgeKey(edge), edge.role)
      }
    }

    for (const node of nodes) {
      const target = !focus || focus.ids.has(node.id) ? 1 : 0
      node.opacity += (target - node.opacity) * (1 - Math.exp(-dt * 2.4))
      const drift = reduce ? 0 : 1
      const x = node.base.x + Math.sin(time * 0.22 + node.phase) * 0.18 * drift
      const y = node.base.y + Math.cos(time * 0.18 + node.phase) * 0.12 * drift
      const z = node.base.z + Math.sin(time * 0.16 + node.phase * 1.4) * 0.14 * drift
      const breath = reduce ? 1 : 1 + Math.sin(time * 0.28 + node.phase) * 0.028
      node.sprite.position.set(x, y, z)
      node.haze.position.set(x, y, z)
      node.pick.position.set(x, y, z)
      node.labelObj.position.set(x, y, z)
      const shown =
        node.kind === 'buyer' ? node.scale * 0.42 : node.kind === 'project' ? node.scale * 1.55 : node.scale * 0.72
      node.sprite.scale.setScalar(shown * breath)
      node.haze.scale.setScalar(node.scale * hazeMul[node.kind] * breath)
      ;(node.sprite.material as THREE.SpriteMaterial).opacity = innerOp[node.kind] * node.opacity
      ;(node.haze.material as THREE.SpriteMaterial).opacity = hazeOp[node.kind] * node.opacity
      const emphasis = hover === node.id || selected.value === node.id ? 1 : 0.76
      node.label.style.opacity = String(Math.max(0, node.opacity) * emphasis)
      node.pick.visible = node.opacity > 0.32
    }

    const endA = new THREE.Vector3()
    const endB = new THREE.Vector3()
    for (const live of lines) {
      live.target = desired.has(live.key) ? (live.label ? 0.32 : 0.2) : 0
      live.opacity += (live.target - live.opacity) * (1 - Math.exp(-dt * 2.1))
      const mat = live.line.material as THREE.LineBasicMaterial
      mat.opacity = live.opacity
      live.line.visible = live.opacity > 0.015
      const pa = nodePos.get(live.a)
      const pb = nodePos.get(live.b)
      if (pa && pb) {
        endA.copy(pa)
        endB.copy(pb)
        const attr = live.line.geometry.getAttribute('position') as THREE.BufferAttribute
        const arr = attr.array as Float32Array
        for (let i = 0; i <= SEG; i++) {
          pointOn(endA, endB, i / SEG, live.a, live.b, scratch)
          arr[i * 3] = scratch.x
          arr[i * 3 + 1] = scratch.y
          arr[i * 3 + 2] = scratch.z
        }
        attr.needsUpdate = true
        pointOn(endA, endB, 0.5, live.a, live.b, scratch)
        live.tagObj.position.copy(scratch)
      }
      const bandRole = bandRoles.get(live.key)
      if (bandRole) {
        const won = bandRole === 'win'
        const bought = bandRole === 'buy'
        live.tag.style.opacity = String(bandFade * (won ? 0.96 : bought ? 0.72 : 0.62))
        live.tag.style.color = won
          ? 'rgba(255, 206, 148, 0.96)'
          : bought
            ? 'rgba(236, 220, 196, 0.82)'
            : 'rgba(214, 208, 198, 0.48)'
      } else {
        live.tag.style.opacity = live.label ? String(Math.min(1, live.opacity * 2.6)) : '0'
        live.tag.style.color = ''
      }
    }

    const bandTarget = useBands ? 1 : 0
    bandFade += (bandTarget - bandFade) * (1 - Math.exp(-dt * 2.2))

    function sprinkle(
      pos: Float32Array,
      col: Float32Array,
      start: number,
      edge: StoryEdge,
      samples: number,
      gain: number,
      cr: number,
      cg: number,
      cb: number,
    ) {
      let n = start
      const pa = nodePos.get(edge.a)
      const pb = nodePos.get(edge.b)
      if (!pa || !pb) return n
      endA.copy(pa)
      endB.copy(pb)
      for (let i = 0; i < samples; i++) {
        if (n >= bandMax) break
        const along = i / samples
        const phase = reduce ? along : (along + time * 0.065) % 1
        pointOn(endA, endB, phase, edge.a, edge.b, scratch)
        const env = Math.sin(Math.PI * ((phase + 1) % 1))
        const wave = 0.28 + 0.72 * Math.pow(Math.sin(phase * Math.PI * 4 - time * 0.9), 2)
        const bright = env * wave * bandFade * gain
        pos[n * 3] = scratch.x
        pos[n * 3 + 1] = scratch.y
        pos[n * 3 + 2] = scratch.z
        col[n * 3] = bright * cr
        col[n * 3 + 1] = bright * cg
        col[n * 3 + 2] = bright * cb
        n++
      }
      return n
    }

    function publish(ribbon: ReturnType<typeof makeRibbon>, count: number) {
      ribbon.geo.setDrawRange(0, count)
      ;(ribbon.geo.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true
      ;(ribbon.geo.getAttribute('color') as THREE.BufferAttribute).needsUpdate = true
      ribbon.points.visible = count > 0
    }

    let winCount = 0
    let bidCount = 0
    if (bandFade > 0.03 && focus) {
      for (const edge of focus.edges) {
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
  }
  raf = requestAnimationFrame(frame)

  stop = () => {
    cancelAnimationFrame(raf)
    ro.disconnect()
    window.removeEventListener('keydown', onKey)
    renderer.domElement.removeEventListener('pointerdown', onDown)
    renderer.domElement.removeEventListener('pointerup', onUp)
    renderer.domElement.removeEventListener('pointermove', onMove)
    controls.dispose()
    pickGeo.dispose()
    pickMat.dispose()
    starTex.dispose()
    discTex.dispose()
    bandTex.dispose()
    cloudTex.dispose()
    dustGeo.dispose()
    ;(dust.material as THREE.Material).dispose()
    winRibbon.geo.dispose()
    bidRibbon.geo.dispose()
    ;(winRibbon.points.material as THREE.Material).dispose()
    ;(bidRibbon.points.material as THREE.Material).dispose()
    for (const node of nodes) {
      ;(node.sprite.material as THREE.Material).dispose()
      ;(node.haze.material as THREE.Material).dispose()
    }
    for (const live of lines) {
      live.line.geometry.dispose()
      ;(live.line.material as THREE.Material).dispose()
    }
    renderer.dispose()
    labels.domElement.remove()
    renderer.domElement.remove()
  }
})

onUnmounted(() => stop())
</script>

<template>
  <div class="sky">
    <div ref="host" class="host" aria-label="关系星图，拖拽旋转，滚轮缩放" />
    <div class="vignette" />
    <div class="chrome">
      <div class="modes">
        <button
          v-for="(item, i) in relations"
          :key="item.id"
          class="scene"
          :class="{ on: mode === item.id }"
          @click="pickMode(item.id)"
        >
          <em>0{{ i + 1 }}</em>
          {{ item.name }}
        </button>
      </div>
      <p class="hint">拖拽旋转 · 滚轮远近</p>
      <div class="legend">
        <span><i class="mark buyer" />采购单位</span>
        <span><i class="mark project" />项目</span>
        <span><i class="mark winner" />中标供应商</span>
        <span><i class="mark bidder" />投标参与方</span>
        <span><i class="mark vendor" />产品供应商</span>
      </div>
    </div>

    <aside class="panel">
      <Transition name="soft" mode="out-in">
        <div :key="mode + (selected ?? '')" class="ask">
          <div class="kicker">任务二 · {{ modeName }}</div>
          <h1>{{ title }}</h1>
          <div class="stats">
            <div v-for="s in stats" :key="s.k">
              <strong>{{ s.v }}</strong>
              <span>{{ s.k }}</span>
            </div>
          </div>
        </div>
      </Transition>
      <div v-if="picked" class="detail">
        <div class="kicker">{{ kindName[picked.kind] }}</div>
        <div class="who">{{ picked.label }}</div>
        <p>{{ picked.note }}</p>
      </div>
      <div class="rows">
        <p v-if="picked && !groups.length" class="empty">
          {{ mode === 'rival' ? '没有参与过的投标项目。' : '没有成交或供应记录。' }}
        </p>
        <template v-if="picked">
          <section v-for="group in groups" :key="group.id" class="group">
            <button class="head" :class="{ on: selected === group.id }" @click="toggle(group.id)">
              <span>
                <b>{{ group.title }}</b>
                <small>{{ group.meta }}</small>
              </span>
            </button>
            <button
              v-for="party in group.parties"
              :key="group.id + party.id"
              :class="{ on: selected === party.id }"
              @click="toggle(party.id)"
            >
              <span>
                <b>{{ party.name }}</b>
                <small>{{ party.meta }}</small>
              </span>
              <em>{{ party.value }}</em>
            </button>
          </section>
        </template>
        <button v-for="row in rosterRows" v-else :key="row.id" @click="toggle(row.id)">
          <span>
            <b>{{ row.name }}</b>
            <small>{{ row.meta }}</small>
          </span>
          <em>{{ row.value }}</em>
        </button>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.sky {
  position: relative;
  height: 100%;
  overflow: hidden;
  background: #0c0b0a;
  color: #f6efe6;
  user-select: none;
}

.host {
  position: absolute;
  inset: 0;
}

.vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(ellipse at 40% 48%, transparent 42%, rgba(6, 5, 4, 0.5) 100%);
}

.chrome {
  position: absolute;
  inset: 0;
  z-index: 2;
  pointer-events: none;
}

.modes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  padding: 18px 22px 0;
}

.scene {
  pointer-events: auto;
  display: flex;
  align-items: baseline;
  gap: 6px;
  border: 0;
  border-bottom: 1px solid transparent;
  background: transparent;
  padding: 6px 0;
  color: #b3a89c;
  font-size: 13px;
  transition: color 0.35s ease, border-color 0.35s ease;
}

.scene em {
  color: #8d8276;
  font-size: 10px;
  font-style: normal;
  letter-spacing: 0.08em;
}

.scene:hover {
  color: #f6efe6;
}

.scene.on {
  border-bottom-color: rgba(246, 239, 230, 0.8);
  color: #f6efe6;
}

.scene.on em {
  color: #e6d3b0;
}

.hint {
  position: absolute;
  bottom: 46px;
  left: 22px;
  margin: 0;
  color: rgba(246, 239, 230, 0.42);
  font-size: 12px;
  letter-spacing: 0.08em;
}

.legend {
  position: absolute;
  bottom: 16px;
  left: 22px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: rgba(246, 239, 230, 0.62);
  font-size: 12px;
}

.legend span {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mark {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 99px;
  background: radial-gradient(circle, rgba(255, 246, 236, 0.9) 0%, rgba(255, 220, 186, 0.25) 26%, transparent 72%);
}

.mark.buyer {
  width: 16px;
  height: 16px;
  background: radial-gradient(circle, rgba(255, 236, 214, 0.28) 0%, rgba(255, 220, 186, 0.08) 46%, transparent 76%);
}

.mark.project {
  width: 15px;
  height: 15px;
  background: radial-gradient(circle, rgba(240, 208, 176, 0.72) 0%, rgba(240, 208, 176, 0.28) 46%, transparent 76%);
}

.mark.bidder {
  opacity: 0.7;
}

.mark.vendor {
  background: radial-gradient(circle, rgba(240, 210, 170, 0.8) 0%, rgba(210, 170, 120, 0.2) 30%, transparent 72%);
}

.panel {
  position: absolute;
  z-index: 3;
  top: 0;
  right: 0;
  bottom: 0;
  display: flex;
  width: min(340px, 100%);
  flex-direction: column;
  border-left: 1px solid rgba(246, 239, 230, 0.08);
  background: rgba(14, 12, 10, 0.58);
  backdrop-filter: blur(16px);
}

.ask {
  padding: 22px 22px 8px;
}

.kicker {
  color: rgba(246, 239, 230, 0.46);
  font-size: 11px;
  letter-spacing: 0.16em;
}

.ask h1 {
  margin: 10px 0 0;
  font-family: "Noto Serif SC", serif;
  font-size: 24px;
  font-weight: 600;
  line-height: 1.35;
}

.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 18px;
}

.stats strong {
  display: block;
  font-family: "Noto Serif SC", serif;
  font-size: 22px;
  font-weight: 600;
}

.stats span {
  display: block;
  margin-top: 4px;
  color: rgba(246, 239, 230, 0.48);
  font-size: 11px;
}

.detail {
  margin: 8px 16px 0;
  border: 1px solid rgba(246, 239, 230, 0.14);
  padding: 12px 12px 10px;
}

.who {
  margin-top: 6px;
  font-family: "Noto Serif SC", serif;
  font-size: 18px;
}

.detail p {
  margin: 6px 0 0;
  color: rgba(246, 239, 230, 0.7);
  font-size: 13px;
  line-height: 1.6;
}

.rows {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 8px 8px 16px;
}

.empty {
  margin: 8px 12px;
  color: rgba(246, 239, 230, 0.55);
  font-size: 13px;
  line-height: 1.6;
}

.group {
  margin-top: 6px;
}

.group .head {
  color: rgba(246, 239, 230, 0.72);
}

.group .head b {
  letter-spacing: 0.04em;
}

.rows button {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 12px;
  border: 0;
  background: transparent;
  padding: 10px 12px;
  color: inherit;
  text-align: left;
  transition: background 0.25s ease, opacity 0.45s ease;
}

.rows button:hover {
  background: rgba(246, 239, 230, 0.05);
}

.rows button.on {
  background: rgba(246, 239, 230, 0.1);
}

.rows b {
  display: block;
  font-weight: 500;
  font-size: 13px;
}

.rows small {
  display: block;
  margin-top: 2px;
  color: rgba(246, 239, 230, 0.48);
  font-size: 12px;
}

.rows em {
  margin-left: auto;
  font-style: normal;
  font-size: 13px;
}

.soft-enter-active,
.soft-leave-active {
  transition: opacity 0.45s ease;
}

.soft-enter-from,
.soft-leave-to {
  opacity: 0;
}

@media (max-width: 960px) {
  .panel {
    top: auto;
    height: 42vh;
    width: 100%;
    border-top: 1px solid rgba(246, 239, 230, 0.08);
    border-left: 0;
  }

  .hint,
  .legend {
    bottom: calc(42vh + 12px);
  }
}
</style>
