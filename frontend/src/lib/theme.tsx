/**
 * 主题状态管理 —— 3 态：'light' | 'dark' | 'system'。
 *
 * - 'system' 跟随 `prefers-color-scheme` media query
 * - 持久化到 `localStorage` key `agenta-theme`
 * - 切换时给 `<html>` 加 / 减 `.dark` class（CSS 已就绪：index.css 里的 `.dark` 选择器）
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

export type Theme = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'agenta-theme'

function isTheme(v: unknown): v is Theme {
  return v === 'light' || v === 'dark' || v === 'system'
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

function resolveEffective(theme: Theme): 'light' | 'dark' {
  if (theme === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light'
  }
  return theme
}

function applyClass(effective: 'light' | 'dark'): void {
  const root = document.documentElement
  if (effective === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
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
