// Run with Playwright MCP browser_run_code_unsafe(filename) and Vite on 127.0.0.1:5181.
// Every API endpoint is mocked/fail-closed; this script never controls a real receiver.
async (page) => {
  const results = []
  for (const scenario of [{ mobile: false, blocked: false }, { mobile: true, blocked: false }, { mobile: false, blocked: true }]) {
    const { mobile, blocked } = scenario
    const context = await page.context().browser().newContext({
      viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 },
      hasTouch: mobile, isMobile: mobile,
    })
    try {
      const testPage = await context.newPage()
      const errors = []
      testPage.on('pageerror', (error) => errors.push(error.message))
      const status = {
        status_version: 9007199254740000, capture_state: 'online', listening_state: 'running', control_requires_token: false,
        last_packet_at: null, slots: [{ slot: 1, tg: 91, active: false }, { slot: 2, tg: 46001, active: false }],
        counts: { calls: 16, translated: 0 }, ai: { transcription: 'local', translation: 'ready', model: '' },
        device: { name: 'Follow preference test', address: 'test.local' },
      }
      const makeCall = (slot, index) => ({
        id: `follow-test-${slot}-${index}`, slot, tg: slot === 1 ? 91 : 46001, source_id: 1000000 + slot,
        callsign: `TEST${slot}`, country: '', country_code: '', name: '', started_at: 1791600000 + index * 5,
        ended_at: 1791600003 + index * 5, duration: 3, status: 'complete',
        original: 'Follow preference regression fixture.\n'.repeat(18), translated: null,
        topic: null, language: 'en', error: null, audio_url: null, direction: 'incoming',
      })
      const records = [1, 2].flatMap((slot) => Array.from({ length: 8 }, (_, index) => makeCall(slot, index)))
      await testPage.addInitScript(({ status, blocked }) => {
        if (blocked) Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new DOMException('Storage disabled for regression test', 'SecurityError') } })
        class Stream extends EventTarget {
          constructor() {
            super()
            window.followTestStream = this
            window.queueMicrotask(() => {
              if (this.closed) return
              this.onopen?.(new Event('open'))
              this.dispatchEvent(new MessageEvent('status', { data: JSON.stringify(status) }))
            })
          }
          sendCall(call) { this.dispatchEvent(new MessageEvent('call', { data: JSON.stringify(call) })) }
          close() { this.closed = true }
        }
        window.EventSource = Stream
      }, { status, blocked })
      let unexpected = 0
      await testPage.route('**/api/**', (route) => { unexpected++; return route.fulfill({ status: 418, json: { error: 'Unexpected API request' } }) })
      await testPage.route('**/api/status', (route) => route.fulfill({ json: status }))
      await testPage.route('**/api/calls?*', (route) => route.fulfill({ json: records }))
      const activate = (locator) => mobile ? locator.tap({ timeout: 5000 }) : locator.click({ timeout: 5000 })
      const loaded = async () => {
        await testPage.locator('.call-card').first().waitFor()
        await testPage.waitForFunction(() => ![...document.querySelectorAll('.slot-footer')].some((element) => element.textContent.includes('同步中')))
      }
      await testPage.goto('http://127.0.0.1:5181/')
      await loaded()
      await testPage.getByRole('button', { name: '暂停追踪', exact: true }).waitFor()
      await activate(testPage.getByRole('button', { name: '暂停追踪', exact: true }))
      await testPage.getByRole('button', { name: '恢复追踪', exact: true }).waitFor()
      const before = await testPage.locator('#slot-panel-1 .slot-feed').evaluate((element) => element.scrollTop)
      const addition = makeCall(1, 20)
      records.push(addition)
      await testPage.evaluate((call) => window.followTestStream.sendCall(call), addition)
      await testPage.locator(`[data-call-id="${addition.id}"]`).waitFor()
      const after = await testPage.locator('#slot-panel-1 .slot-feed').evaluate((element) => element.scrollTop)
      if (Math.abs(after - before) > 1) throw new Error('Paused feed scrolled when a new call arrived')
      await testPage.reload()
      await loaded()
      if (blocked) {
        await testPage.getByRole('button', { name: '暂停追踪', exact: true }).waitFor()
      } else {
        await testPage.getByRole('button', { name: '恢复追踪', exact: true }).waitFor()
        const paused = await testPage.locator('.feed-follow-state').allTextContents()
        if (paused.some((label) => label !== '追踪暂停')) throw new Error('Both slots must remain paused after refresh')
        await activate(testPage.getByRole('button', { name: '恢复追踪', exact: true }))
        await testPage.reload()
        await loaded()
        await testPage.getByRole('button', { name: '暂停追踪', exact: true }).waitFor()
      }
      if (errors.length) throw new Error(errors.join('\n'))
      if (unexpected) throw new Error('Unexpected API requests escaped explicit mocks')
      results.push({ viewport: mobile ? '390x844 touch' : '1440x900 mouse', blockedStorage: blocked,
        defaultFollow: true, pausedDoesNotScroll: true, pauseAndResumePersist: !blocked, pageErrors: errors.length, unexpectedApiRequests: unexpected })
    } finally {
      await context.close()
    }
  }
  return results
}
