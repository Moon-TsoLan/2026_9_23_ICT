<script setup lang="ts">
import { ArrowRight } from 'lucide-vue-next'
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api/client'
import { sessionChanged, useAuth } from '@/composables/useAuth'

const route = useRoute()
const { sessionError } = useAuth()
const registering = ref(false)
const username = ref(''), password = ref(''), confirmation = ref('')
const busy = ref(false), error = ref('')

function switchMode() { registering.value = !registering.value; error.value = ''; confirmation.value = '' }
async function submit() {
  if (busy.value) return
  error.value = ''
  if (registering.value && password.value !== confirmation.value) { error.value = '两次输入的密码不一致'; return }
  busy.value = true
  try {
    const method = registering.value ? api.register : api.login
    await method(username.value.trim(), password.value)
    sessionChanged()
    const next = String(route.query.next ?? '')
    // A full navigation clears graph/session caches before another account can see them.
    window.location.replace(next.startsWith('/') && !next.startsWith('//') && !next.includes('\\') && !next.startsWith('/login') ? next : '/ingest')
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败，请重试' }
  finally { busy.value = false }
}

// ---------- 以下仅为装饰：整页星空（呼应 03 探索的宇宙域），不参与任何业务逻辑 ----------
// 只用 Canvas 2D，不引 three：星图那一包仍然只随 /explore 加载。

/** 与主导航同序同名，登录前先让人看到里面有什么 */
const MODULES = [
  { no: '01', label: '采集' },
  { no: '02', label: '检索' },
  { no: '03', label: '探索' },
]

type StarKind = 'buyer' | 'project' | 'supplier'
type LinkRole = 'buy' | 'win' | 'bid'
interface Star { x: number; y: number; z: number; kind: StarKind; phase: number; sx: number; sy: number; k: number; near: number }
interface Link { a: number; b: number; role: LinkRole; phase: number }

/** 与 StarMap / 探索页图例同值 */
const STAR_RGB: Record<StarKind, string> = { buyer: '84,196,255', project: '255,200,97', supplier: '238,243,251' }
const LINK_RGB: Record<LinkRole, string> = { buy: '121,208,255', win: '255,200,97', bid: '142,166,196' }
const LINK_ALPHA: Record<LinkRole, number> = { buy: 0.4, win: 0.68, bid: 0.24 }
const STAR_SIZE: Record<StarKind, number> = { buyer: 104, project: 64, supplier: 36 }
/** 入场次序（秒）：先亮采购单位，再连到项目，最后连到供应商 */
const STAR_AT: Record<StarKind, number> = { buyer: 0.2, project: 0.9, supplier: 1.6 }
const LINK_AT: Record<LinkRole, number> = { buy: 0.5, win: 1.2, bid: 1.7 }
const TAU = Math.PI * 2
const FOCAL = 2.6

/** 固定种子：每次打开都是同一片星空 */
function seeded(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const ease = (value: number) => 1 - (1 - Math.min(1, Math.max(0, value))) ** 3

/** 示意星系：采购单位 → 项目 → 供应商；少数供应商跨簇复用，形成"关联" */
function buildGalaxy() {
  const rand = seeded(20261110)
  const stars: Star[] = []
  const links: Link[] = []
  const add = (x: number, y: number, z: number, kind: StarKind) =>
    stars.push({ x, y, z, kind, phase: rand() * TAU, sx: 0, sy: 0, k: 1, near: 1 }) - 1
  const pool: number[] = []
  const BUYERS = 6
  for (let b = 0; b < BUYERS; b++) {
    const angle = (b / BUYERS) * TAU + rand() * 0.5
    const radius = 0.42 + rand() * 0.34
    const bx = Math.cos(angle) * radius, bz = Math.sin(angle) * radius, by = (rand() - 0.5) * 0.9
    const buyer = add(bx, by, bz, 'buyer')
    const projects = 2 + Math.floor(rand() * 3)
    for (let p = 0; p < projects; p++) {
      const pa = rand() * TAU, pr = 0.2 + rand() * 0.2
      const px = bx + Math.cos(pa) * pr, pz = bz + Math.sin(pa) * pr, py = by + (rand() - 0.5) * 0.44
      const project = add(px, py, pz, 'project')
      links.push({ a: buyer, b: project, role: 'buy', phase: rand() })
      const bidders = 2 + Math.floor(rand() * 3)
      for (let s = 0; s < bidders; s++) {
        let supplier: number
        // 跨簇复用多落在"投标"上（淡线），偶尔才是"中标"（金线），画面不至于缠成一团
        if (pool.length > 4 && rand() < (s === 0 ? 0.14 : 0.34)) supplier = pool[Math.floor(rand() * pool.length)]!
        else {
          const sa = rand() * TAU, sr = 0.14 + rand() * 0.2
          supplier = add(px + Math.cos(sa) * sr, py + (rand() - 0.5) * 0.36, pz + Math.sin(sa) * sr, 'supplier')
          pool.push(supplier)
        }
        if (links.some((link) => link.a === project && link.b === supplier)) continue
        links.push({ a: project, b: supplier, role: s === 0 ? 'win' : 'bid', phase: rand() })
      }
    }
  }
  const reach = Math.max(...stars.map((star) => Math.hypot(star.x, star.y, star.z)))
  for (const star of stars) { star.x /= reach; star.y /= reach; star.z /= reach }
  return { stars, links }
}

function buildDust() {
  const rand = seeded(923)
  return Array.from({ length: 260 }, (_, i) => ({
    u: rand(), v: rand(), depth: 0.2 + rand() * 0.8, size: 0.6 + rand() * 1.2,
    alpha: 0.1 + rand() * 0.5, speed: 0.4 + rand() * 1.4, phase: rand() * TAU, far: i % 16 === 0,
  }))
}

/** 辉光贴图：亮核 + 晕，叠加混合后相邻的星会自然晕在一起 */
function glowSprite(rgb: string) {
  const size = 128, half = size / 2
  const sprite = document.createElement('canvas')
  sprite.width = sprite.height = size
  const g = sprite.getContext('2d')!
  const halo = g.createRadialGradient(half, half, 0, half, half, half)
  for (const [stop, alpha] of [[0, 1], [0.05, 0.8], [0.14, 0.32], [0.36, 0.09], [1, 0]] as const) halo.addColorStop(stop, `rgba(${rgb},${alpha})`)
  g.fillStyle = halo
  g.fillRect(0, 0, size, size)
  const core = g.createRadialGradient(half, half, 0, half, half, 7)
  core.addColorStop(0, 'rgba(255,255,255,0.95)')
  core.addColorStop(1, 'rgba(255,255,255,0)')
  g.fillStyle = core
  g.fillRect(0, 0, size, size)
  return sprite
}

/** 启动星空渲染，返回清理函数。开启"减少动效"时只画一帧静止画面。 */
function startSky(canvas: HTMLCanvasElement): () => void {
  const context = canvas.getContext('2d')
  if (!context) return () => {}
  const ctx = context
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const { stars, links } = buildGalaxy()
  const dust = buildDust()
  const sprites: Record<StarKind, HTMLCanvasElement> = {
    buyer: glowSprite(STAR_RGB.buyer), project: glowSprite(STAR_RGB.project), supplier: glowSprite(STAR_RGB.supplier),
  }
  const pulseSprite: Record<LinkRole, HTMLCanvasElement> = { buy: sprites.buyer, win: sprites.project, bid: sprites.supplier }
  const started = performance.now()
  let w = 0, h = 0, dpr = 1, raf = 0
  let tiltX = 0, tiltY = 0, aimX = 0, aimY = 0

  function glow(sprite: HTMLCanvasElement, x: number, y: number, size: number, alpha: number) {
    if (alpha <= 0.004 || size <= 0.5) return
    ctx.globalAlpha = Math.min(1, alpha)
    ctx.drawImage(sprite, x - size / 2, y - size / 2, size, size)
  }

  function draw(t: number) {
    if (!w || !h) return
    tiltX += (aimX - tiltX) * 0.045
    tiltY += (aimY - tiltY) * 0.045
    const narrow = w < 900
    const cx = narrow ? w * 0.5 : w * 0.38
    const cy = narrow ? Math.min(h * 0.3, 250) : h * 0.47
    const scale = (narrow ? Math.min(w * 0.62, 300) : Math.min(w * 0.4, h * 0.6)) * (0.78 + 0.22 * ease(t / 2.8))
    const dim = narrow ? 0.7 : 1

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    ctx.globalCompositeOperation = 'lighter'

    // 星尘：越"近"的视差越大，缓慢横移
    ctx.fillStyle = 'rgb(246,239,230)'
    const dustIn = ease(t / 1.6)
    for (const d of dust) {
      const x = ((d.u + t * 0.0018 * d.depth) % 1) * (w + 80) - 40 - tiltX * 26 * d.depth
      const y = d.v * (h + 80) - 40 - tiltY * 26 * d.depth
      const alpha = d.alpha * dustIn * (0.55 + 0.45 * Math.sin(t * d.speed + d.phase))
      // 少数远星带一点晕，其余只是一个点
      if (d.far) glow(sprites.supplier, x, y, 9 + d.size * 5, alpha * 0.9)
      else {
        ctx.globalAlpha = alpha
        ctx.fillRect(x, y, d.size, d.size)
      }
    }

    // 投影：绕竖轴慢转 + 固定俯角，鼠标只做小幅视差
    const rotY = t * 0.05 + tiltX * 0.35, rotX = 0.5 + tiltY * 0.2
    const cosY = Math.cos(rotY), sinY = Math.sin(rotY), cosX = Math.cos(rotX), sinX = Math.sin(rotX)
    for (const star of stars) {
      const x1 = star.x * cosY + star.z * sinY, z1 = star.z * cosY - star.x * sinY
      const y2 = star.y * cosX - z1 * sinX, z2 = star.y * sinX + z1 * cosX
      star.k = FOCAL / (FOCAL + z2)
      star.sx = cx + x1 * scale * star.k
      star.sy = cy + y2 * scale * star.k
      star.near = 0.38 + 0.62 * (1 - (z2 + 1.1) / 2.2)
    }

    // 连线：入场时从起点画向终点
    for (const link of links) {
      const a = stars[link.a]!, b = stars[link.b]!
      const grow = ease((t - LINK_AT[link.role] - link.phase * 0.5) / 0.9)
      if (grow <= 0) continue
      ctx.globalAlpha = LINK_ALPHA[link.role] * ((a.near + b.near) / 2) * dim
      ctx.strokeStyle = `rgb(${LINK_RGB[link.role]})`
      ctx.lineWidth = link.role === 'win' ? 1.25 : 1
      ctx.beginPath()
      ctx.moveTo(a.sx, a.sy)
      ctx.lineTo(a.sx + (b.sx - a.sx) * grow, a.sy + (b.sy - a.sy) * grow)
      ctx.stroke()
    }

    // 流光：采购 → 项目 → 中标供应商，带一小段拖尾
    if (!still && t > 2.6) {
      for (const link of links) {
        if (link.role === 'bid') continue
        const a = stars[link.a]!, b = stars[link.b]!
        const head = ((t / (5 + link.phase * 4) + link.phase) % 1) * 3
        if (head > 1.2) continue
        const k = (a.k + b.k) / 2
        for (let i = 0; i < 5; i++) {
          const at = head - i * 0.035
          if (at < 0 || at > 1) continue
          glow(pulseSprite[link.role], a.sx + (b.sx - a.sx) * at, a.sy + (b.sy - a.sy) * at,
            (24 - i * 3.4) * k, Math.sin(Math.PI * at) * (0.95 - i * 0.18) * dim)
        }
      }
    }

    // 星：按远近缩放明暗，缓慢呼吸
    for (const star of stars) {
      const shown = ease((t - STAR_AT[star.kind] - (star.phase / TAU) * 0.5) / 0.8)
      const breath = 1 + 0.12 * Math.sin(t * 1.1 + star.phase)
      glow(sprites[star.kind], star.sx, star.sy, STAR_SIZE[star.kind] * star.k * breath * (0.6 + 0.4 * shown), star.near * shown * dim)
    }
    ctx.globalAlpha = 1
  }

  function resize() {
    w = canvas.clientWidth
    h = canvas.clientHeight
    dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = Math.round(w * dpr)
    canvas.height = Math.round(h * dpr)
    draw(still ? 40 : (performance.now() - started) / 1000)
  }
  function loop(now: number) {
    draw((now - started) / 1000)
    raf = requestAnimationFrame(loop)
  }
  function onPointer(event: PointerEvent) {
    aimX = (event.clientX / window.innerWidth - 0.5) * 2
    aimY = (event.clientY / window.innerHeight - 0.5) * 2
  }

  const observer = new ResizeObserver(resize)
  observer.observe(canvas)
  resize()
  if (!still) {
    window.addEventListener('pointermove', onPointer, { passive: true })
    raf = requestAnimationFrame(loop)
  }
  return () => {
    cancelAnimationFrame(raf)
    observer.disconnect()
    window.removeEventListener('pointermove', onPointer)
  }
}

const sky = ref<HTMLCanvasElement | null>(null)
let stopSky = () => {}
onMounted(() => { if (sky.value) stopSky = startSky(sky.value) })
onUnmounted(() => stopSky())
</script>

<template>
  <main class="login" data-domain="void">
    <div class="mist" aria-hidden="true"><i /><i /><i /></div>
    <canvas ref="sky" class="sky" aria-hidden="true" />
    <div class="veil" aria-hidden="true" />

    <header class="brand">
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
        <rect x="1.25" y="1.25" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.4" />
        <circle cx="14.5" cy="14.5" r="6" fill="currentColor" />
      </svg>
      <div>
        标络
        <small>采招实体关系分析平台</small>
      </div>
    </header>

    <div class="stage">
      <div class="pitch">
        <p class="kicker">采购公告 · 实体与关系</p>
        <h1 class="h-serif"><span>从采购信息，</span><span>发现关联。</span></h1>
      </div>

      <section class="sheet">
        <p class="kicker">{{ registering ? '建立你的数据空间' : '欢迎回来' }}</p>
        <h2 class="h-serif">{{ registering ? '注册账号' : '登录标络' }}</h2>
        <p class="help">使用账号管理和查看你的采购数据。</p>
        <form @submit.prevent="submit">
          <div class="field">
            <label for="username">用户名</label>
            <input id="username" v-model="username" class="input" name="username" required minlength="3" maxlength="32" autocomplete="username" placeholder="请输入用户名" :disabled="busy" />
          </div>
          <div class="field">
            <label for="password">密码</label>
            <input id="password" v-model="password" class="input" name="password" type="password" required minlength="6" maxlength="128" :autocomplete="registering ? 'new-password' : 'current-password'" placeholder="请输入密码" :disabled="busy" />
          </div>
          <div v-if="registering" class="field">
            <label for="confirmation">确认密码</label>
            <input id="confirmation" v-model="confirmation" class="input" type="password" required maxlength="128" autocomplete="new-password" placeholder="再次输入密码" :disabled="busy" />
          </div>
          <p v-if="error || sessionError" class="error" role="alert">{{ error || sessionError }}</p>
          <button class="btn submit" type="submit" :disabled="busy">
            {{ busy ? '请稍候…' : registering ? '注册并进入' : '登录' }}
            <ArrowRight v-if="!busy" :size="14" aria-hidden="true" />
          </button>
        </form>
        <p class="switch">{{ registering ? '已有账号？' : '还没有账号？' }}<button type="button" :disabled="busy" @click="switchMode">{{ registering ? '去登录' : '注册账号' }}</button></p>
      </section>
    </div>

    <footer class="foot">
      <ul class="legend">
        <li><i class="mark buyer" />采购单位</li>
        <li><i class="mark project" />项目</li>
        <li><i class="mark supplier" />供应商</li>
      </ul>
      <ul class="modules">
        <li v-for="item in MODULES" :key="item.no"><em>{{ item.no }}</em>{{ item.label }}</li>
      </ul>
    </footer>
  </main>
</template>

<style scoped>
/* 整页宇宙域：星空铺满，表单是浮在上面的一块毛玻璃，不再有明暗接缝。
   变量与 tokens.css 的 .shell[data-domain="void"] 同值；通用类（.input / .btn / .kicker / .h-serif）照常复用。 */
.login {
  --paper: #0c0b0a;
  --surface: #141110;
  --ink: #f6efe6;
  --muted: rgba(246, 239, 230, 0.55);
  --faint: rgba(246, 239, 230, 0.32);
  --line: rgba(246, 239, 230, 0.1);
  --accent: #ffce94;
  --bad: #f87171;
  --gutter: clamp(24px, 4vw, 56px);

  position: relative;
  isolation: isolate;
  display: flex;
  min-height: 100%;
  flex-direction: column;
  overflow: hidden;
  background: var(--paper);
  color: var(--ink);
}

/* ---------- 背景层：雾 → 星空画布 → 暗角与颗粒 ---------- */
.mist,
.sky,
.veil {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.mist {
  z-index: -4;
  overflow: hidden;
}

.mist i {
  position: absolute;
  border-radius: 50%;
  will-change: transform;
  animation: drift 36s ease-in-out infinite alternate;
}

.mist i:nth-child(1) {
  top: -12%;
  left: 10%;
  width: 64vmax;
  height: 64vmax;
  background: radial-gradient(closest-side, rgba(255, 200, 97, 0.15), rgba(255, 200, 97, 0.045) 55%, transparent);
}

.mist i:nth-child(2) {
  top: 30%;
  left: -18%;
  width: 56vmax;
  height: 56vmax;
  background: radial-gradient(closest-side, rgba(84, 196, 255, 0.12), rgba(84, 196, 255, 0.035) 55%, transparent);
  animation-duration: 44s;
  animation-direction: alternate-reverse;
}

.mist i:nth-child(3) {
  top: -24%;
  right: -12%;
  width: 52vmax;
  height: 52vmax;
  background: radial-gradient(closest-side, rgba(255, 138, 92, 0.1), rgba(255, 138, 92, 0.03) 55%, transparent);
  animation-duration: 52s;
}

.sky {
  z-index: -3;
  width: 100%;
  height: 100%;
}

/* 暗角 + 细颗粒合在一层（颗粒给大面积暗色渐变打散色带；透明度直接烤进噪声，省一次整屏离屏合成） */
.veil {
  z-index: -1;
  background:
    radial-gradient(ellipse at 40% 46%, transparent 40%, rgba(6, 5, 4, 0.58) 100%),
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .045 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}

/* ---------- 品牌位：与内部顶栏同高同样式 ---------- */
.brand {
  display: flex;
  height: 60px;
  flex-shrink: 0;
  align-items: center;
  gap: 10px;
  padding: 0 var(--gutter);
  font-family: var(--font-serif);
  font-weight: 600;
  letter-spacing: -0.03em;
}

.brand small {
  display: block;
  margin-top: 2px;
  color: var(--muted);
  font-family: var(--font-sans);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.14em;
}

/* ---------- 主区：左下标语，右侧玻璃卡 ---------- */
.stage {
  display: flex;
  min-height: 0;
  flex: 1;
  justify-content: space-between;
  gap: 40px;
  padding: 24px var(--gutter) 32px;
}

.pitch {
  align-self: flex-end;
}

.pitch .kicker {
  animation: bloom 900ms var(--ease) 500ms backwards;
}

.pitch h1 {
  margin-top: 14px;
  font-size: clamp(32px, 3.6vw, 54px);
  line-height: 1.28;
}

/* 渐变字要落在每一行自己身上：父级裁字遇到带滤镜动画的子元素会失效 */
.pitch h1 span {
  display: block;
  background: linear-gradient(100deg, var(--c-winner) 20%, var(--warm) 120%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: bloom 1100ms var(--ease) 700ms backwards;
}

.pitch h1 span + span {
  animation-delay: 950ms;
}

.sheet {
  position: relative;
  width: 400px;
  max-width: 100%;
  flex-shrink: 0;
  align-self: center;
  margin-right: clamp(0px, 3vw, 56px);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: linear-gradient(155deg, rgba(246, 239, 230, 0.09), rgba(246, 239, 230, 0.025) 42%, rgba(12, 11, 10, 0.32));
  padding: 36px;
  box-shadow:
    0 32px 90px rgba(0, 0, 0, 0.55),
    inset 0 1px 0 rgba(255, 246, 236, 0.1);
  -webkit-backdrop-filter: blur(16px);
  backdrop-filter: blur(16px);
  animation: bloom 1000ms var(--ease) 350ms backwards;
}

/* 沿卡片边框游走的一段光 */
.sheet::before {
  content: "";
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  padding: 1px;
  background: conic-gradient(
    from var(--spin),
    transparent 0 60%,
    rgba(255, 206, 148, 0.85) 74%,
    rgba(84, 196, 255, 0.55) 83%,
    transparent 94%
  );
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
  animation: spin 9s linear infinite;
}

.sheet h2 {
  margin-top: 8px;
  font-size: 28px;
  line-height: 1.3;
}

.help {
  margin: 8px 0 28px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 16px;
}

.field label {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.04em;
}

.field .input {
  height: 42px;
  border-color: rgba(246, 239, 230, 0.13);
  background: rgba(246, 239, 230, 0.045);
  padding: 0 12px;
  font-size: 14px;
  transition:
    border-color var(--dur-fast) var(--ease),
    background var(--dur-fast) var(--ease),
    box-shadow var(--dur-base) var(--ease);
}

.field .input:hover:not(:disabled) {
  border-color: rgba(246, 239, 230, 0.28);
}

.field .input:focus {
  border-color: var(--accent);
  background: rgba(246, 239, 230, 0.07);
  box-shadow:
    0 0 0 3px rgba(255, 206, 148, 0.14),
    0 0 26px rgba(255, 206, 148, 0.14);
}

.field .input:disabled {
  opacity: 0.6;
}

/* 浏览器自动填充会强行换底色：把这次换色拖到几乎永不发生，玻璃质感得以保留 */
.field .input:-webkit-autofill {
  -webkit-text-fill-color: var(--ink);
  caret-color: var(--ink);
  transition:
    background-color 600000s 0s,
    border-color var(--dur-fast) var(--ease),
    box-shadow var(--dur-base) var(--ease);
}

.error {
  margin-bottom: 12px;
  border-radius: var(--r-sm);
  background: color-mix(in srgb, var(--bad) 12%, transparent);
  padding: 8px 10px;
  color: var(--bad);
  font-size: 12px;
  line-height: 1.6;
}

.submit {
  position: relative;
  width: 100%;
  height: 42px;
  margin-top: 8px;
  overflow: hidden;
  border-color: transparent;
  background: linear-gradient(180deg, var(--c-winner), var(--c-vendor));
  font-size: 14px;
  font-weight: 500;
  box-shadow:
    0 0 0 1px rgba(255, 246, 236, 0.2),
    0 10px 30px rgba(255, 206, 148, 0.18);
  transition: box-shadow var(--dur-base) var(--ease);
}

.submit:hover:not(:disabled) {
  opacity: 1;
  box-shadow:
    0 0 0 1px rgba(255, 246, 236, 0.4),
    0 10px 44px rgba(255, 206, 148, 0.42);
}

.submit:disabled {
  cursor: wait;
}

/* 按钮上间歇扫过的一道高光 */
.submit::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(105deg, transparent 35%, rgba(255, 255, 255, 0.8) 50%, transparent 65%);
  transform: translateX(-120%);
  pointer-events: none;
  animation: sheen 5.5s var(--ease) 2.6s infinite;
}

.switch {
  margin-top: 20px;
  color: var(--muted);
  font-size: 13px;
}

.switch button {
  margin-left: 6px;
  color: var(--ink);
  text-decoration: underline;
  text-decoration-color: var(--faint);
  text-underline-offset: 4px;
  transition:
    color var(--dur-fast) var(--ease),
    text-decoration-color var(--dur-fast) var(--ease);
}

.switch button:hover:not(:disabled) {
  color: var(--accent);
  text-decoration-color: var(--accent);
}

.switch button:disabled {
  opacity: 0.6;
  cursor: wait;
}

/* ---------- 底栏：左图例（同探索页），右导航编号（同主导航） ---------- */
.foot {
  display: flex;
  height: 52px;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  border-top: 1px solid var(--line);
  padding: 0 var(--gutter);
  font-size: 12px;
}

.legend,
.modules {
  display: flex;
  align-items: center;
}

.legend {
  gap: 20px;
  color: rgba(246, 239, 230, 0.62);
}

.legend li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.mark {
  display: inline-block;
  border-radius: 99px;
}

.mark.buyer {
  width: 16px;
  height: 16px;
  background: radial-gradient(circle, #54c4ff 0%, rgba(84, 196, 255, 0.45) 34%, rgba(84, 196, 255, 0.12) 62%, transparent 78%);
}

.mark.project {
  width: 13px;
  height: 13px;
  background: radial-gradient(circle, #ffc861 0%, rgba(255, 200, 97, 0.45) 36%, rgba(255, 200, 97, 0.12) 64%, transparent 80%);
}

.mark.supplier {
  width: 9px;
  height: 9px;
  background: radial-gradient(circle, #eef3fb 0%, rgba(238, 243, 251, 0.5) 40%, transparent 80%);
}

.modules {
  gap: 24px;
  color: var(--muted);
}

.modules em {
  margin-right: 6px;
  color: var(--faint);
  font-size: 10px;
  font-style: normal;
  letter-spacing: 0.08em;
}

/* ---------- 动效 ---------- */
@property --spin {
  syntax: "<angle>";
  inherits: false;
  initial-value: 0deg;
}

@keyframes spin {
  to {
    --spin: 360deg;
  }
}

/* 从一团光晕里显影 */
@keyframes bloom {
  from {
    opacity: 0;
    filter: blur(14px);
    transform: translateY(12px);
  }
}

@keyframes drift {
  from {
    transform: translate3d(-4%, -3%, 0) scale(1);
  }

  to {
    transform: translate3d(5%, 4%, 0) scale(1.14);
  }
}

@keyframes sheen {
  0% {
    transform: translateX(-120%);
  }

  22%,
  100% {
    transform: translateX(120%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .mist i,
  .pitch .kicker,
  .pitch h1 span,
  .sheet,
  .sheet::before,
  .submit::after {
    animation: none;
  }
}

/* ---------- 窄屏：标语在上，玻璃卡在下 ---------- */
@media (max-width: 900px) {
  .stage {
    flex-direction: column;
    justify-content: flex-start;
    gap: 28px;
    padding-top: 12px;
  }

  .pitch {
    align-self: flex-start;
  }

  .pitch h1 {
    margin-top: 10px;
    font-size: 30px;
  }

  .sheet {
    width: 100%;
    max-width: 420px;
    margin: 0 auto;
    padding: 28px;
  }

  .legend {
    display: none;
  }
}
</style>
