/** Browser regression using static build + intercepted synthetic responses only.
 * No live application, database, extraction API or OCR server is contacted.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import http from 'node:http'
import { createRequire } from 'node:module'
import os from 'node:os'

const moduleRoot = process.env.PLAYWRIGHT_MODULE_ROOT || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node')
const require = createRequire(path.join(moduleRoot, 'package.json'))
const { chromium } = require('playwright')
const root = path.resolve('web/dist')
const out = path.resolve('work/explore-20261014')
await fs.mkdir(out, { recursive: true })
const server = http.createServer(async (req, res) => {
  const requested = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname)
  if (!requested.startsWith(root + path.sep) && requested !== root) { res.writeHead(403).end(); return }
  let file = requested
  try { if (!(await fs.stat(file)).isFile()) file = path.join(root, 'index.html') }
  catch { file = path.join(root, 'index.html') }
  const ext = path.extname(file)
  res.setHeader('Content-Type', ext === '.js' ? 'text/javascript' : ext === '.css' ? 'text/css' : 'text/html')
  res.end(await fs.readFile(file))
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] })
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
await page.route('https://fonts.loli.net/**', route => route.fulfill({ status: 200, contentType: 'text/css', body: '' }))
const errors = [], requests = [], passed = []
let expectingConflict = false
page.on('pageerror', error => errors.push(String(error)))
page.on('console', message => {
  if (message.type() === 'error' && !(expectingConflict && message.text().includes('409'))) errors.push(message.text())
})
const n = (id, label, kind = 'winner') => ({ id, label, kind, weight: 1 })
const stress = process.env.EXPLORE_STRESS === '1'
const projects = Array.from({ length: stress ? 100 : 6 }, (_, i) => n('proj:' + i, '项目' + i + ' · 设备采购包', 'project'))
const resident = Array.from({ length: stress ? 101 : 1 }, (_, i) => n('sup:常驻公司' + i, '常驻公司' + i))
const base = { nodes: [n('buyer:采购单位', '采购单位', 'buyer'), ...projects, ...resident],
  edges: [...projects.map(p => ({ a: p.id, b: 'buyer:采购单位', role: 'buy', weight: 1 })),
    ...resident.map((s, i) => ({ a: projects[i % projects.length].id, b: s.id, role: 'win', weight: 1 }))] }
const names = ['低频甲公司', '低频乙公司', '慢公司', '快公司', '分页公司']
let version = '50:'
await page.route('**/api/**', async route => {
  const url = new URL(route.request().url()); requests.push(url.pathname)
  const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  if (url.pathname === '/api/auth/me') return send({ user: { account_id: 'test', username: '测试账号', tenant_id: 'test' } })
  if (url.pathname === '/api/health') return send({ ok: true, announcements: 50, data_version: version })
  if (url.pathname === '/api/stats/distribution') return send({ categories: [], brands: [], purchasers: [], winners: [] })
  if (url.pathname === '/api/graph/overview') return send({ elapsed_ms: 1, graph: { ...base, highlight_ids: [] },
    meta: { purchasers: 1, projects: projects.length, suppliers_total: resident.length + 5,
      suppliers_shown: resident.length, sampled: true, data_version: version } })
  if (url.pathname === '/api/parties') return send({ items: names.filter(name => name.includes(url.searchParams.get('q')))
    .map(name => ({ id: 'sup:' + name, name, kind: 'winner' })) })
  if (url.pathname.startsWith('/api/parties/')) {
    const id = decodeURIComponent(url.pathname.slice('/api/parties/'.length))
    return send({ id, name: id.slice(4), kind: 'winner', stats: [{ k: '项目', v: '1' }], facts: ['模拟档案'] })
  }
  if (url.pathname.startsWith('/api/graph/neighborhood/')) {
    if (url.searchParams.get('expected_version') !== version) return send({ detail: '资料库已更新，请刷新星图后继续补载' }, 409)
    const id = decodeURIComponent(url.pathname.slice('/api/graph/neighborhood/'.length))
    const name = id.slice(id.indexOf(':') + 1); const offset = Number(url.searchParams.get('offset') || 0)
    if (name === '慢公司') await new Promise(resolve => setTimeout(resolve, 1800))
    const total = name === '分页公司' ? 35 : 1
    const items = id.startsWith('proj:') ? [n('sup:低频乙公司', '低频乙公司')] :
      Array.from({ length: Math.min(30, total - offset) }, (_, i) => offset + i < 6 ? projects[offset + i] :
        n('proj:' + (offset + i), '项目' + (offset + i) + ' · 设备采购包', 'project'))
    const node = id.startsWith('proj:') ? projects[Number(name)] : n(id, name)
    return send({ node, data_version: version, graph: { nodes: [node, ...items],
      edges: items.map(p => ({ a: p.id, b: id, role: 'win', weight: 1 })) },
      pagination: { offset, limit: 30, total, has_more: offset + 30 < total } })
  }
  if (url.pathname.startsWith('/api/scenes/')) {
    const body = route.request().postDataJSON()
    if (body.expected_version !== version) return send({ detail: '资料库已更新，请刷新星图后继续补载' }, 409)
    const scene = url.pathname.split('/')[3]
    return send({ scene, data_version: version, elapsed_ms: 1,
      graph: { nodes: body.subjects.map(name => n('sup:' + name, name)), edges: [], highlight_ids: [] },
      narrative: { title: '模拟空交集', stats: [{ k: '共同项目', v: '0' }], ranking: [] } })
  }
  if (url.pathname === '/api/objects/search') return send({ total: 0, items: [], page: 1, page_size: 30 })
  return send({ detail: 'Unexpected fixture request ' + url.pathname }, 500)
})
const state = () => page.locator('.statusbar .state')
async function pick(name) {
  await page.locator('.scenebar input').fill(name)
  const option = page.locator('.scenebar .drop button').filter({ hasText: name }).first()
  await option.waitFor({ state: 'visible' }); await option.click()
}
async function settled(text) {
  await page.waitForFunction(text => document.querySelector('.statusbar .state')?.textContent?.includes(text), text)
  await page.waitForTimeout(650)
}
try {
  await page.goto(origin + '/explore')
  await page.locator('.star-label').first().waitFor({ state: 'attached' })
  await settled('未提问')
  const baseline = await page.locator('.star-label').first().elementHandle()
  await pick('低频甲公司'); await settled('低频甲公司')
  assert.equal(await baseline.evaluate(el => el.isConnected), true)
  assert.match(await page.locator('.statusbar').textContent(), new RegExp(`${base.nodes.length + 1} 节点`))
  passed.push('低频目标补载且底图星体DOM保持原对象')
  await page.screenshot({ path: path.join(out, '01-picked.png') })
  await page.getByRole('button', { name: '选多家，查共同合作的采购单位' }).click()
  await pick('低频乙公司'); await settled('2/4')
  assert.equal(await page.locator('.basket-sec .chip').count(), 2)
  await page.getByRole('button', { name: '查看全部已选' }).click()
  await page.waitForTimeout(800)
  for (const name of names.slice(0,2)) {
    const label = page.locator('.star-label').filter({ hasText: name.replace(/公司$/, '') }).first()
    await label.waitFor({ state: 'visible' })
    assert.equal(await label.isVisible(), true)
    const box = await label.boundingBox(); assert.ok(box.x >= 0 && box.y >= 0 && box.x < 1600 && box.y < 950)
  }
  await page.locator('.legend button').filter({ hasText: '供应商' }).click()
  for (const name of names.slice(0,2))
    await page.locator('.star-label').filter({ hasText: name.replace(/公司$/, '') }).first().waitFor({ state: 'visible' })
  passed.push('全部对比主体就绪、标注可见并可共同取景')
  const beforeReturn = await page.locator('.star-label').filter({ hasText: '低频乙' }).first().boundingBox()
  await page.screenshot({ path: path.join(out, '02-basket.png') })
  await page.getByRole('link', { name: '02 检索' }).click()
  await page.getByRole('link', { name: '03 探索' }).click()
  await settled('2/4')
  assert.equal(await page.locator('.basket-sec .chip').count(), 2)
  assert.equal(requests.filter(p => p === '/api/graph/overview').length, 1)
  await page.waitForTimeout(800)
  const afterReturn = await page.locator('.star-label').filter({ hasText: '低频乙' }).first().boundingBox()
  assert.ok(Math.abs(beforeReturn.x - afterReturn.x) < 12 && Math.abs(beforeReturn.y - afterReturn.y) < 12)
  passed.push('换页返回恢复篮中主体，未重新采样底图')
  await page.getByRole('button', { name: '查询交集' }).click(); await settled('交集 0')
  assert.equal(await page.locator('.basket-sec .chip').count(), 2)
  passed.push('空交集仍保留全部对比主体')
  await page.screenshot({ path: path.join(out, '03-empty-comparison.png') })
  await page.getByRole('button', { name: '回到全景' }).click(); await settled('未提问')
  assert.match(await page.locator('.statusbar').textContent(), new RegExp(`${base.nodes.length} 节点`))
  passed.push('退出移除临时星体，恢复原底图规模')
  await pick('慢公司'); await page.waitForTimeout(100)
  await pick('快公司'); await settled('快公司'); await page.waitForTimeout(2200)
  assert.match(await state().textContent(), /快公司/)
  passed.push('迟到的旧目标请求不能夺回焦点')
  await pick('分页公司'); await settled('分页公司')
  assert.match(await page.locator('.coverage-bar').textContent(), /30 \/ 35/)
  const preserved = await page.locator('.star-label').first().elementHandle()
  await page.getByRole('button', { name: '加载更多关系' }).click(); await settled('分页公司')
  await page.waitForFunction(() => document.querySelector('.coverage-bar')?.textContent?.includes('35 / 35'))
  assert.equal(await preserved.evaluate(el => el.isConnected), true)
  passed.push('分页加载追加临时星体，不销毁已有星体')
  await page.getByRole('button', { name: '查看完整档案' }).click()
  await page.locator('.party .picker input').fill('低频甲公司')
  await page.locator('.party .drop button').filter({ hasText: '低频甲公司' }).click()
  await page.getByRole('button', { name: '在星图中查看' }).click()
  await settled('低频甲公司')
  assert.match(await state().textContent(), /S0/)
  assert.equal(await page.locator('.basket-sec .chip').count(), 0)
  passed.push('档案页明确的新目标覆盖旧问题，沿用会话底图')
  await page.goto(origin + '/explore?focus=' + encodeURIComponent('proj:0'))
  await settled('S6')
  assert.match(await page.locator('.srel-item').first().textContent(), /低频乙公司/)
  passed.push('项目详情通过独立补载显示未进入采样的投标人')
  await page.goto(origin + '/explore'); await settled('未提问')
  assert.match(await page.locator('.statusbar').textContent(), new RegExp(`${base.nodes.length} 节点`))
  passed.push('整页重新加载建立新会话，临时主体不残留')
  await pick('分页公司'); await settled('分页公司')
  expectingConflict = true
  version = '51:'
  await pick('低频乙公司')
  await page.getByText('资料库已更新。请点击刷新星图后再继续，当前问题暂时保留。').waitFor()
  assert.match(await state().textContent(), /分页公司/)
  passed.push('数据库版本变化拒绝混入新数据并保留当前问题')
  assert.deepEqual(errors, [])
  await fs.writeFile(path.join(out, stress ? 'stress-browser-report.json' : 'browser-report.json'),
    JSON.stringify({ base_nodes: base.nodes.length, passed, requests, errors }, null, 2))
  console.log(JSON.stringify({ passed, errors }, null, 2))
} catch (error) {
  await page.screenshot({ path: path.join(out, 'failure.png') })
  await fs.writeFile(path.join(out, 'failure.json'), JSON.stringify({ passed, errors, error: String(error),
    text: await page.locator('body').innerText() }, null, 2))
  throw error
} finally {
  await browser.close(); await new Promise(resolve => server.close(resolve))
}
