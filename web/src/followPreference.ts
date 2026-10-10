const storageKey = 'starwave.follow'

export function readFollowPreference(): boolean {
  try {
    return localStorage.getItem(storageKey) !== 'false'
  } catch {
    return true
  }
}

export function saveFollowPreference(follow: boolean): void {
  try {
    localStorage.setItem(storageKey, String(follow))
  } catch {
    // Keep the control usable when browser storage is disabled.
  }
}
