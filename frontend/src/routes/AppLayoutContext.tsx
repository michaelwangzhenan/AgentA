import { createContext, useContext } from 'react'

import type { Message } from '@/types/chat'
import type { Session } from '@/types/session'

export type AppLayoutContextValue = {
  sessions: Session[]
  sessionsReady: boolean
  activeSessionId: string | null
  messages: Message[]
  inFlight: boolean
  hasMoreOlder: boolean
  loadingOlder: boolean
  loadOlderMessages: () => void | Promise<void>
  send: (text: string) => void
  stop: () => void
  regenerate: (assistantId: string) => void
  editResend: (userId: string, newText: string) => void
  resendUser: (userId: string) => void
  switchVersion: (assistantId: string, index: number) => void
  handleSelectSession: (id: string) => void
  handleCreateSession: () => Promise<void>
  handleRenameSession: (id: string, title: string) => Promise<void>
  handleDeleteSession: (id: string) => Promise<void>
}

const AppLayoutContext = createContext<AppLayoutContextValue | null>(null)

export function AppLayoutProvider({
  value,
  children,
}: {
  value: AppLayoutContextValue
  children: React.ReactNode
}) {
  return <AppLayoutContext.Provider value={value}>{children}</AppLayoutContext.Provider>
}

export function useAppLayout(): AppLayoutContextValue {
  const ctx = useContext(AppLayoutContext)
  if (!ctx) throw new Error('useAppLayout 必须在 AppLayout 内使用')
  return ctx
}
