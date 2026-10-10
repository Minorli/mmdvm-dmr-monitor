// Run with the Playwright MCP browser_run_code tool using this file's absolute filename.
// A Vite server must be listening on 127.0.0.1:5181. All API traffic is isolated/mocked.
async (page) => {
  const results = []
  for (const mobile of [false, true]) {
    const context = await page.context().browser().newContext({
      viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 },
      hasTouch: mobile,
      isMobile: mobile,
    })
    try {
      const testPage = await context.newPage()
      const status = {
        status_version: 9007199254740000,
        capture_state: 'offline', listening_state: 'stopped', control_requires_token: false,
        last_packet_at: null, slots: [{ slot: 1, tg: 91, active: false }, { slot: 2, tg: 46001, active: false }],
        counts: { calls: 0, translated: 0 }, ai: { transcription: 'paused', translation: 'disabled', model: '' },
        device: { name: 'Connection interaction test', address: 'test.local' },
      }
      const profile = { host: 'test.local', port: 22, username: 'pi-star', auth_mode: 'key', password_configured: false, key_available: true, configured: true }
      await testPage.addInitScript(({ status }) => {
        class Stream extends EventTarget {
          constructor() {
            super()
            window.queueMicrotask(() => {
              if (this.closed) return
              this.onopen?.(new Event('open'))
              this.dispatchEvent(new MessageEvent('status', { data: JSON.stringify(status) }))
            })
          }
          close() { this.closed = true }
        }
        window.EventSource = Stream
      }, { status })
      let writes = 0
      await testPage.route('**/api/**', (route) => {
        writes++
        return route.fulfill({ status: 418, json: { error: 'Unexpected API request in isolated interaction test' } })
      })
      await testPage.route('**/api/status', (route) => route.fulfill({ json: status }))
      await testPage.route('**/api/calls?*', (route) => route.fulfill({ json: [] }))
      await testPage.route('**/api/connection', (route) => {
        if (route.request().method() !== 'GET') writes++
        return route.fulfill({ json: profile })
      })
      await testPage.route('**/api/connection/test', (route) => { writes++; return route.fulfill({ json: { ok: true, message: 'test only' } }) })
      await testPage.goto('http://127.0.0.1:5181/')
      await testPage.getByRole('button', { name: '开始监听', exact: true }).waitFor()
      await testPage.waitForFunction(() => !document.querySelector('.header-tools .arco-btn-loading'))
      const activate = (locator) => mobile ? locator.tap({ timeout: 5000 }) : locator.click({ timeout: 5000 })
      await activate(testPage.getByRole('button', { name: '连接设置', exact: true }))
      await testPage.getByRole('textbox', { name: '设备地址', exact: true }).waitFor()
      await testPage.waitForFunction(() => !document.querySelector('.connection-dialog .connection-state-note')?.textContent.includes('正在读取'))
      await activate(testPage.locator('.connection-fields .arco-select'))
      await activate(testPage.getByRole('option', { name: '密码', exact: true }))
      const password = testPage.getByRole('textbox', { name: 'SSH口令', exact: true })
      await password.waitFor({ state: 'visible' })
      if (await password.inputValue() !== '') throw new Error('Password must never be prefilled')
      const overflow = await testPage.evaluate(() => document.documentElement.scrollWidth > innerWidth)
      if (overflow) throw new Error('Connection dialog overflows the viewport')
      await activate(testPage.getByRole('button', { name: '关闭', exact: true }))
      if (writes) throw new Error('Selecting auth mode must not write configuration or test SSH')
      results.push({ viewport: mobile ? '390x844 touch' : '1440x900 mouse', keyToPassword: true, passwordEmpty: true, writes, overflow })
    } finally {
      await context.close()
    }
  }
  return results
}
