import { afterEach, describe, expect, it, vi } from 'vitest'
import { readFollowPreference, saveFollowPreference } from './followPreference'

afterEach(() => vi.unstubAllGlobals())

describe('follow preference across page loads', () => {
  it('starts following for a new browser and retains both pause and resume choices', () => {
    const values = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value) },
    })
    expect(readFollowPreference()).toBe(true)
    saveFollowPreference(false)
    expect(readFollowPreference()).toBe(false)
    saveFollowPreference(true)
    expect(readFollowPreference()).toBe(true)
  })

  it('uses the default when stored data is invalid', () => {
    vi.stubGlobal('localStorage', { getItem: () => 'invalid' })
    expect(readFollowPreference()).toBe(true)
  })

  it('keeps the page and toggle usable when browser storage is blocked', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('Storage blocked') },
      setItem: () => { throw new Error('Storage blocked') },
    })
    expect(readFollowPreference()).toBe(true)
    expect(() => saveFollowPreference(false)).not.toThrow()
  })
})
