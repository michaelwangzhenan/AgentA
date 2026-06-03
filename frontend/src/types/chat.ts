export type Role = 'user' | 'assistant'

export type Message = {
  id: string
  role: Role
  content: string
}

export type ChatRequest = {
  message: string
}

export type ChatResponse = {
  reply: string
  session_id: string
}
