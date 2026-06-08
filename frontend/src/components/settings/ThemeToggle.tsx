import { Coffee, Flame, Monitor, Moon, Sun, Sunrise, Sunset } from 'lucide-react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useTheme, type Theme } from '@/lib/theme'

type Option = { value: Theme; label: string; icon: React.ReactNode }

const OPTIONS: Option[] = [
  { value: 'light', label: '浅色', icon: <Sun className="h-4 w-4" /> },
  { value: 'dark', label: '深色', icon: <Moon className="h-4 w-4" /> },
  { value: 'warm-light', label: '暖色·浅', icon: <Coffee className="h-4 w-4" /> },
  { value: 'warm-dark', label: '暖色·深', icon: <Flame className="h-4 w-4" /> },
  { value: 'amber-light', label: '橙调·浅', icon: <Sunrise className="h-4 w-4" /> },
  { value: 'amber-dark', label: '橙调·深', icon: <Sunset className="h-4 w-4" /> },
  { value: 'system', label: '跟随系统', icon: <Monitor className="h-4 w-4" /> },
]

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const current =
    OPTIONS.find((o) => o.value === theme) ??
    OPTIONS[OPTIONS.length - 1]

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="rounded-md p-1.5 hover:bg-accent"
        title={`主题：${current.label}`}
        aria-label="切换主题"
      >
        {current.icon}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {OPTIONS.map((opt) => (
          <DropdownMenuItem
            key={opt.value}
            onClick={() => setTheme(opt.value)}
            className={theme === opt.value ? 'font-medium' : ''}
          >
            <span className="mr-2 inline-flex">{opt.icon}</span>
            {opt.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
