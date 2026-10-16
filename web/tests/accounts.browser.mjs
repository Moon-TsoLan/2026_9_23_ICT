/** Real browser -> local static/proxy -> isolated account test API. No extraction calls.
 * Run only with ICT_ACCOUNT_TEST_API and ICT_BROWSER_TEST_USER_A/B set for a scratch DB.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import http from 'node:http'
import os from 'node:os'
import { createRequire } from 'node:module'

const target = new URL(process.env.ICT_ACCOUNT_TEST_API || 'http://127.0.0.1:8001')
assert(['localhost', '127.0.0.1'].includes(target.hostname), 'Local isolated API only')
const alice = process.env.ICT_BROWSER_TEST_USER_A, bob = process.env.ICT_BROWSER_TEST_USER_B
assert(alice && bob, 'Provide users seeded by tests/test_account_database.py')
const moduleRoot = process.env.PLAYWRIGHT_MODULE_ROOT || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node')
const { chromium } = createRequire(path.join(moduleRoot, 'package.json'))('playwright')
const root = path.resolve('web/dist'), out = path.resolve('work/accounts-20261015')
await fs.mkdir(out, { recursive: true })
const server = http.createServer(async (req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: req.headers }, upstream => { res.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(res) })
    proxy.on('error', error => { res.writeHead(502); res.end(error.message) }); req.pipe(proxy); return
  }
  let file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname)
  if (!file.startsWith(root + path.sep) && file !== root) { res.writeHead(403).end(); return }
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
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } })
await context.route('https://fonts.loli.net/**', route => route.fulfill({ status: 200, contentType: 'text/css', body: '' }))
const page = await context.newPage(), passed = [], errors = []
page.on('pageerror', error => errors.push(String(error)))
const login = async name => {
  await page.locator('#username').fill(name)
  await page.locator('#password').fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.waitForURL(url => url.pathname !== '/login')
  await page.locator('.account-menu').getByText(name, { exact: true }).waitFor()
}
try {
  await page.goto(origin + '/search')
  await page.waitForURL('**/login?**')
  await page.getByRole('heading', { name: '登录标络' }).waitFor()
  await page.screenshot({ path: path.join(out, 'login.png'), fullPage: true })
  passed.push('未登录深链跳转登录页')
  await page.locator('#username').fill(alice); await page.locator('#password').fill('wrong-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: '用户名或密码错误' }).waitFor()
  passed.push('错误密码提示')
  await login(alice)
  await page.getByRole('cell', { name: '甲专属标的', exact: true }).waitFor()
  assert.equal(await page.getByRole('cell', { name: '乙专属标的', exact: true }).count(), 0)
  await page.reload(); await page.getByRole('cell', { name: '甲专属标的', exact: true }).waitFor()
  await page.screenshot({ path: path.join(out, 'account-search.png'), fullPage: true })
  passed.push('真实Cookie登录、回到原检索深链、刷新保持会话与账号数据')
  const second = await context.newPage(); await second.goto(origin + '/search')
  await second.getByRole('cell', { name: '甲专属标的', exact: true }).waitFor()
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await page.waitForURL('**/login'); await second.waitForURL('**/login')
  await second.close()
  passed.push('退出同步清除另一标签页的旧账号界面')
  await page.getByRole('button', { name: '注册账号', exact: true }).click()
  await page.locator('#username').fill(alice); await page.locator('#password').fill('test-password')
  await page.locator('#confirmation').fill('different-password')
  await page.getByRole('button', { name: '注册并进入' }).click()
  await page.getByRole('alert').filter({ hasText: '两次输入的密码不一致' }).waitFor()
  await page.locator('#confirmation').fill('test-password')
  await page.getByRole('button', { name: '注册并进入' }).click()
  await page.getByRole('alert').filter({ hasText: '用户名或数据空间已被使用' }).waitFor()
  passed.push('简单注册表单确认密码和重名提示')
  const fresh = 'browser_' + Date.now()
  await page.locator('#username').fill(fresh)
  await page.getByRole('button', { name: '注册并进入' }).click()
  await page.waitForURL('**/ingest')
  await page.locator('.account-menu').getByText(fresh, { exact: true }).waitFor()
  await Promise.all([page.waitForResponse(r => r.url().includes('/api/objects/search') && r.status() === 200), page.goto(origin + '/search')])
  assert.equal(await page.getByRole('cell', { name: '甲专属标的', exact: true }).count(), 0)
  assert.equal(await page.getByRole('cell', { name: '乙专属标的', exact: true }).count(), 0)
  passed.push('注册即登录，新账号数据为空')
  await page.getByRole('button', { name: '退出', exact: true }).click(); await page.waitForURL('**/login')
  await login(bob); await page.goto(origin + '/search')
  await page.getByRole('cell', { name: '乙专属标的', exact: true }).waitFor()
  assert.equal(await page.getByRole('cell', { name: '甲专属标的', exact: true }).count(), 0)
  passed.push('同一浏览器切换账号后不残留旧检索结果')
  await Promise.all([page.waitForResponse(r => r.url().includes('/api/graph/overview') && r.status() === 200), page.goto(origin + '/explore')])
  assert.equal(await page.getByText('甲专属项目', { exact: true }).count(), 0)
  passed.push('登录后可进入真实数据库探索页')
  await page.getByRole('button', { name: '退出', exact: true }).click(); await page.waitForURL('**/login')
  await page.goto(origin + '/explore'); await page.waitForURL('**/login?**')
  passed.push('退出后探索深链重新要求登录')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: path.join(out, 'login-mobile.png'), fullPage: true })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  passed.push('移动端登录页无横向溢出')
  assert.deepEqual(errors, [])
  const report = { passed, errors, frontend: 'production build', api: 'isolated real PostgreSQL, no models/OCR' }
  await fs.writeFile(path.join(out, 'browser-report.json'), JSON.stringify(report, null, 2))
  process.stdout.write(JSON.stringify(report, null, 2) + '\n')
} catch (error) {
  await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true }).catch(() => {})
  throw error
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)) }
