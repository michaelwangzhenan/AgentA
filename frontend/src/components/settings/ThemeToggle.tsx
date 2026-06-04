import { Monitor, Moon, Sun } from 'lucide-react'

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
  { value: 'system', label: '跟随系统', icon: <Monitor className="h-4 w-4" /> },
]

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const current = OPTIONS.find((o) => o.value === theme) ?? OPTIONS[2]

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className="rounded-md p-1.5 hover:bg-accent"
          title={`主题：${current.label}`}
          aria-label="切换主题"
        >
          {current.icon}
        </button>
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
