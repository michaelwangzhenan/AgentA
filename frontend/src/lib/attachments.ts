import type { MessageAttachment } from '@/types/chat'

// 跟 Composer.buildMessage 的序列化格式对齐：
//   文本附件： \n\n附件 `name`：\n```\n<content>\n```
//   图片/二进制：\n\n[附件 name（图片|二进制）未随消息发送：暂不支持多模态]
const TEXT_ATT_RE = /\n\n附件 `([^`]+)`：\n```\n([\s\S]*?)\n```/g
const OTHER_ATT_RE = /\n\n\[附件 ([^（]+)（(图片|二进制)）未随消息发送[^\]]*\]/g

/**
 * 把发给后端的完整用户消息拆成「展示文本 + 附件元数据」。
 * 后端只存这一整串，所以发送时和加载历史时都走这个函数，保证展示一致。
 */
export function parseUserMessage(content: string): {
  text: string
  attachments: MessageAttachment[]
} {
  const attachments: MessageAttachment[] = []
  let firstIdx = content.length

  TEXT_ATT_RE.lastIndex = 0
  for (let m: RegExpExecArray | null; (m = TEXT_ATT_RE.exec(content)); ) {
    firstIdx = Math.min(firstIdx, m.index)
    const body = m[2]
    attachments.push({
      name: m[1],
      kind: 'text',
      lines: body.length ? body.split('\n').length : 0,
      sent: true,
    })
  }

  OTHER_ATT_RE.lastIndex = 0
  for (let m: RegExpExecArray | null; (m = OTHER_ATT_RE.exec(content)); ) {
    firstIdx = Math.min(firstIdx, m.index)
    attachments.push({
      name: m[1],
      kind: m[2] === '图片' ? 'image' : 'other',
      sent: false,
    })
  }

  const text = (firstIdx < content.length ? content.slice(0, firstIdx) : content).trim()
  return { text, attachments }
}

/** 文件名取扩展名做角标（如 x.txt → TXT），无扩展名退回 FILE。 */
export function fileBadge(name: string): string {
  const ext = name.includes('.') ? name.split('.').pop() : ''
  return (ext || 'file').toUpperCase().slice(0, 4)
}
