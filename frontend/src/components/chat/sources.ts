// 后端 CitationBuilder 在正文末尾追加的来源块标题（src/agent/core/citation_builder.py）
const SOURCES_HEADER = '— sources —'

export type SourceLine = { num: string; text: string }

/** 把回答正文拆成「正文」+「来源条目」两部分。无来源块时 sources 为空。 */
export function parseSources(content: string): {
  body: string
  sources: SourceLine[]
} {
  const idx = content.indexOf(SOURCES_HEADER)
  if (idx < 0) return { body: content, sources: [] }
  const body = content.slice(0, idx).trimEnd()
  const tail = content.slice(idx + SOURCES_HEADER.length)
  const sources: SourceLine[] = []
  for (const raw of tail.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const m = /^\[(\d+)\]\s*(.*)$/.exec(line)
    if (m) sources.push({ num: m[1], text: m[2] })
  }
  return { body, sources }
}
