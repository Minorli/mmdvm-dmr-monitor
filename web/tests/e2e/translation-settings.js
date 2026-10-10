// Playwright MCP regression; run against Vite 127.0.0.1:5181.
// All /api traffic is fail-closed/mocked. No real provider, control, or settings requests.
async (page) => {
  const results = []
  for (const mobile of [false, true]) {
    const context = await page.context().browser().newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 }, hasTouch: mobile, isMobile: mobile })
    try {
      const p = await context.newPage()
      const errors = []
      p.on('pageerror', (error) => errors.push(error.message))
      let status = { status_version: 9007199254740000, capture_state: 'offline', listening_state: 'stopped', control_requires_token: false,
        last_packet_at: null, slots: [{ slot: 1, tg: 91, active: false }, { slot: 2, tg: 46001, active: false }], counts: { calls: 0, translated: 0 },
        ai: { transcription: 'paused', translation: 'disabled', model: '' }, device: { name: 'Settings regression', address: 'device.example.net' } }
      let settings = { base_url: 'https://api.example.com/v1', model: 'example-model', api_mode: 'responses', enabled: true, api_key_configured: true }
      let reject = 0
      let unexpected = 0
      const writes = []
      const getTokens = []
      await p.addInitScript(({ status }) => {
        class Stream extends EventTarget {
          constructor() { super(); window.translationTestStream = this; window.queueMicrotask(() => { if (!this.closed) { this.onopen?.(new Event('open')); this.send(status) } }) }
          send(value) { this.dispatchEvent(new MessageEvent('status', { data: JSON.stringify(value) })) }
          close() { this.closed = true }
        }
        window.EventSource = Stream
      }, { status })
      await p.route('**/api/**', (r) => { unexpected++; return r.fulfill({ status: 418, json: { error: 'Unexpected API request' } }) })
      await p.route('**/api/status', (r) => r.fulfill({ json: status }))
      await p.route('**/api/calls?*', (r) => r.fulfill({ json: [] }))
      await p.route('**/api/connection', (r) => r.fulfill({ json: { host: 'device.example.net', port: 22, username: 'pi-star', auth_mode: 'key', password_configured: false, key_available: true, configured: true } }))
      await p.route('**/api/translation-settings', (r) => {
        if (r.request().method() === 'GET') { getTokens.push(r.request().headers().authorization); return r.fulfill({ json: { ...settings, api_key: 'must-not-be-prefilled' } }) }
        const body = r.request().postDataJSON()
        writes.push(body)
        if (reject) { const code = reject; reject = 0; return r.fulfill({ status: code, json: { detail: code === 401 ? 'must-not-echo-secret' : 'busy' } }) }
        const same = body.base_url === settings.base_url
        settings = { base_url: body.base_url, model: body.model, api_mode: body.api_mode, enabled: body.enabled,
          api_key_configured: body.clear_api_key ? false : !!body.api_key || same && settings.api_key_configured }
        status = { ...status, status_version: status.status_version + 1 }
        return r.fulfill({ json: { ok: true, ...settings } })
      })
      const activate = (element) => mobile ? element.tap({ timeout: 5000 }) : element.click({ timeout: 5000 })
      await p.goto('http://127.0.0.1:5181/')
      await p.getByRole('button', { name: '开始监听', exact: true }).waitFor()
      await activate(p.getByRole('button', { name: '翻译设置', exact: true }))
      const key = p.getByRole('textbox', { name: 'API Key', exact: true })
      await key.waitFor()
      await p.waitForFunction(() => !document.querySelector('.translation-dialog .connection-state-note')?.textContent.includes('正在读取'))
      if (await key.inputValue()) throw new Error('Stored API keys must not be prefilled')
      await p.getByRole('textbox', { name: '模型名称', exact: true }).fill('second-model')
      await activate(p.locator('.translation-dialog .arco-select'))
      await activate(p.getByRole('option', { name: 'Chat Completions API', exact: true }))
      await activate(p.getByRole('button', { name: '保存翻译设置', exact: true }))
      await p.getByText('翻译设置已保存。', { exact: true }).waitFor()
      if ('api_key' in writes[0]) throw new Error('Model/mode change should retain the stored key without reading it')
      await key.fill('test-only-unsaved-key')
      await p.getByRole('textbox', { name: 'API基础地址', exact: true }).fill('https://other.example.com')
      if (await key.inputValue()) throw new Error('Changing the URL must clear the key input')
      const before = writes.length
      await activate(p.getByRole('button', { name: '保存翻译设置', exact: true }))
      await p.getByRole('alert').filter({ hasText: '启用新地址不能复用旧地址' }).waitFor()
      if (writes.length !== before) throw new Error('New enabled provider must require a new key before requesting save')
      await key.fill('test-only-replacement')
      await activate(p.getByRole('button', { name: '保存翻译设置', exact: true }))
      await p.getByText('翻译设置已保存。', { exact: true }).waitFor()
      if (await key.inputValue()) throw new Error('Saving must clear the key input')
      await activate(p.locator('.translation-dialog .arco-checkbox').filter({ hasText: '清除已保存Key' }))
      await activate(p.getByRole('button', { name: '保存翻译设置', exact: true }))
      await p.getByRole('alert').filter({ hasText: '先关闭翻译' }).waitFor()
      await activate(p.locator('.translation-dialog .arco-checkbox').filter({ hasText: '启用中文翻译' }))
      await activate(p.getByRole('button', { name: '保存翻译设置', exact: true }))
      await p.getByText('翻译设置已保存。', { exact: true }).waitFor()
      if (!writes.at(-1).clear_api_key || writes.at(-1).enabled || settings.api_key_configured) throw new Error('Explicit clear must disable/remove the saved key')
      reject = 401
      await activate(p.getByRole('button', { name: '保存翻译设置', exact: true }))
      await p.getByRole('alert').filter({ hasText: '控制凭据不正确' }).waitFor()
      await p.getByRole('textbox', { name: '翻译设置控制凭据', exact: true }).fill('test-only-control')
      await activate(p.getByRole('button', { name: '保存翻译设置', exact: true }))
      await p.getByText('翻译设置已保存。', { exact: true }).waitFor()
      reject = 409
      await activate(p.getByRole('button', { name: '保存翻译设置', exact: true }))
      await p.getByRole('alert').filter({ hasText: '请先停止监听' }).waitFor()
      await key.fill('test-only-close-key')
      await activate(p.getByRole('button', { name: '关闭', exact: true }))
      await activate(p.getByRole('button', { name: '翻译设置', exact: true }))
      await key.waitFor()
      await p.waitForFunction(() => !document.querySelector('.translation-dialog .connection-state-note')?.textContent.includes('正在读取'))
      if (await key.inputValue()) throw new Error('Closing/reopening must clear the key input')
      if (getTokens.at(-1) !== 'Bearer test-only-control') throw new Error('Control credentials must reuse App memory')
      status = { ...status, status_version: status.status_version + 1, listening_state: 'running' }
      await p.evaluate((value) => window.translationTestStream.send(value), status)
      if (!await p.getByRole('button', { name: '保存翻译设置', exact: true }).isDisabled()) throw new Error('Running receiver must not allow save')
      const overflow = await p.evaluate(() => document.documentElement.scrollWidth > innerWidth)
      const keys = await p.evaluate(() => Object.keys(localStorage))
      if (keys.some((name) => !['starwave.theme', 'starwave.follow'].includes(name))) throw new Error('Secrets must not enter localStorage')
      if (overflow || errors.length || unexpected) throw new Error(`Unexpected regression failures: overflow=${overflow}, errors=${errors.length}, api=${unexpected}`)
      results.push({ viewport: mobile ? '390x844 touch' : '1440x900 mouse', storedKeyNeverRead: true, providerBinding: true,
        clearAndDisable: true, tokenSharedInMemory: true, busyConflict: true, overflow, pageErrors: errors.length, unexpectedApiRequests: unexpected })
    } finally { await context.close() }
  }
  return results
}
