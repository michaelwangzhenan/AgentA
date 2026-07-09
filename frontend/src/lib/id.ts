/**
 * 生成一个仅供前端本地使用的 ID（React key / Map 去重 / 消息编辑定位等），不用于鉴权或签名。
 *
 * `crypto.randomUUID()` 只在安全上下文（https:// 或 localhost）可用，裸 IP + http 部署时会
 * 抛 `TypeError: crypto.randomUUID is not a function`。`crypto.getRandomValues()` 没有这个限制，
 * 在明文 http 下也能用，所以不可用时改用它按 RFC 4122 v4 规则手工拼出格式相同的 UUID。
 *
 * 背景见 docs/v_1_1/interation/i1.1_6_randomUUID.md
 */
export function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }

  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }

  console.warn('[id] 当前浏览器随机数能力受限（getRandomValues 也不可用），ID 非密码学强度')
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`
}
