/**
 * 主题状态管理。
 *
 * - 内置 'light' / 'dark' / 'system'（system 跟随 `prefers-color-scheme`）
 * - 皮肤形如 `<名>-light` / `<名>-deep`，用 `data-theme` 覆写一套语义色（见 index.css）；
 *   凡深色基底的皮肤（值里含 `-dark`）额外挂 `.dark`，让硬编码的 `dark:` 类、
 *   CodeMirror、Toaster 都走深色逻辑（浅色皮肤不挂，走浅色逻辑）
 * - 持久化到 `localStorage` key `agenta-theme`
 *
 * 用 React Context 共享 —— 否则 ThemeToggle 改主题时 App.tsx 拿到的 theme 不会
 * 同步更新，导致 Toaster 等 root 组件不及时响应（同一 hook 调两次是两份 state）。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

export type Theme =
  | 'light'
  | 'dark'
  | 'warm-light'
  | 'warm-dark'
  | 'amber-light'
  | 'amber-dark'
  | 'system'

/** 实际生效的渲染主题（'system' 解析后会落到 light / dark）。 */
type EffectiveTheme = Exclude<Theme, 'system'>

const STORAGE_KEY = 'agenta-theme'

const THEMES: readonly Theme[] = [
  'light',
  'dark',
  'warm-light',
  'warm-dark',
  'amber-light',
  'amber-dark',
  'system',
]

function isTheme(v: unknown): v is Theme {
  return typeof v === 'string' && (THEMES as readonly string[]).includes(v)
}

function readStored(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (isTheme(v)) return v
  } catch {
    // localStorage 不可用（隐私模式 / SSR）—— 用默认
  }
  return 'system'
}

function resolveEffective(theme: Theme): EffectiveTheme {
  if (theme === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light'
  }
  return theme
}

function applyClass(effective: EffectiveTheme): void {
  const root = document.documentElement
  // 深色基底（dark 及任何 *-dark 皮肤）挂 `.dark`，让 dark: 类、CodeMirror、Toaster 走深色逻辑
  root.classList.toggle(
    'dark',
    effective === 'dark' || effective.endsWith('-dark'),
  )
  // 皮肤用 data-theme 标记触发 index.css 覆写层；内置 light/dark 不带标记
  if (effective === 'light' || effective === 'dark') {
    delete root.dataset.theme
  } else {
    root.dataset.theme = effective
  }
}

type ThemeContextValue = {
  theme: Theme
  setTheme: (next: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => readStored())

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // localStorage 不可用 —— 仅当前会话生效
    }
  }, [])

  useEffect(() => {
    applyClass(resolveEffective(theme))
  }, [theme])

  useEffect(() => {
    if (theme !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = () => applyClass(resolveEffective('system'))
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [theme])

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme }),
    [theme, setTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const v = useContext(ThemeContext)
  if (!v) {
    throw new Error('useTheme 必须放在 <ThemeProvider> 内')
  }
  return v
}
