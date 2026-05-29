export type ThemePreference = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'gg-dashboard-theme'

export function getThemePreference(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return 'system'
}

export function applyThemePreference(pref: ThemePreference): void {
  if (pref === 'system') {
    document.documentElement.removeAttribute('data-theme')
    localStorage.removeItem(STORAGE_KEY)
    return
  }
  document.documentElement.setAttribute('data-theme', pref)
  localStorage.setItem(STORAGE_KEY, pref)
}

export function initThemeFromStorage(): void {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') {
    document.documentElement.setAttribute('data-theme', stored)
  }
}
