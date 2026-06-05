/**
 * MarkdownEditor —— CodeMirror 6 包装，提供 markdown 语法高亮 + 主题跟随。
 *
 * 主题判定：监听 `<html>` 的 class 变化（ThemeProvider 通过 `.dark` class 切换）；
 * 这样不依赖 ThemeContext，编辑器可以在任何上下文里使用。
 */
import { useEffect, useState } from 'react'
import CodeMirror, { type ReactCodeMirrorProps } from '@uiw/react-codemirror'
import { markdown } from '@codemirror/lang-markdown'
import { oneDark } from '@codemirror/theme-one-dark'
import { EditorView } from '@codemirror/view'
import { cn } from '@/lib/utils'

export type MarkdownEditorProps = {
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  placeholder?: string
  minHeight?: string
  /** true: 容器 + CodeMirror 撑满父高度（依赖父级 flex 子元素 min-h-0）；忽略 minHeight。*/
  fillHeight?: boolean
  className?: string
}

function useIsDark(): boolean {
  const [isDark, setIsDark] = useState(() =>
    typeof document !== 'undefined' &&
    document.documentElement.classList.contains('dark'),
  )
  useEffect(() => {
    const root = document.documentElement
    const obs = new MutationObserver(() => {
      setIsDark(root.classList.contains('dark'))
    })
    obs.observe(root, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return isDark
}

const baseExtensions: ReactCodeMirrorProps['extensions'] = [
  markdown(),
  EditorView.lineWrapping,
]

export function MarkdownEditor({
  value,
  onChange,
  disabled = false,
  placeholder,
  minHeight = '240px',
  fillHeight = false,
  className,
}: MarkdownEditorProps) {
  const isDark = useIsDark()
  const wrapperStyle: React.CSSProperties = fillHeight
    ? { height: '100%' }
    : {}
  const cmStyle: React.CSSProperties = fillHeight
    ? { fontSize: '12px', height: '100%' }
    : { fontSize: '12px', minHeight }
  return (
    <div
      className={cn(
        'rounded-md border border-input bg-background overflow-hidden',
        'focus-within:ring-2 focus-within:ring-ring',
        fillHeight && 'h-full flex flex-col',
        className,
      )}
      style={wrapperStyle}
    >
      <CodeMirror
        value={value}
        onChange={onChange}
        readOnly={disabled}
        editable={!disabled}
        theme={isDark ? oneDark : 'light'}
        placeholder={placeholder}
        extensions={baseExtensions}
        height={fillHeight ? '100%' : undefined}
        basicSetup={{
          lineNumbers: false,
          foldGutter: false,
          highlightActiveLine: false,
          highlightActiveLineGutter: false,
        }}
        style={cmStyle}
        className={fillHeight ? 'flex-1 min-h-0' : undefined}
      />
    </div>
  )
}
