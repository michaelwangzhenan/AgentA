import { useRef, useState } from 'react'
import { Loader2, Upload } from 'lucide-react'
import { cn } from '@/lib/utils'

// 跟后端 SUPPORTED_EXTENSIONS 对齐（src/rag/parser.py）
const ACCEPT_EXTENSIONS = [
  '.md',
  '.txt',
  '.html',
  '.htm',
  '.pdf',
  '.docx',
  '.pptx',
  '.xlsx',
]

export type DropZoneProps = {
  onFiles: (files: File[]) => void | Promise<void>
  disabled?: boolean
}

export function DropZone({ onFiles, disabled = false }: DropZoneProps) {
  const [dragActive, setDragActive] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return
    const arr = Array.from(files)
    onFiles(arr)
  }

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 text-center transition-colors',
        dragActive
          ? 'border-primary bg-primary/5'
          : 'border-border bg-muted/20 hover:bg-muted/30',
        disabled && 'cursor-not-allowed opacity-50',
      )}
      onDragOver={(e) => {
        e.preventDefault()
        if (disabled) return
        setDragActive(true)
      }}
      onDragLeave={(e) => {
        e.preventDefault()
        setDragActive(false)
      }}
      onDrop={(e) => {
        e.preventDefault()
        setDragActive(false)
        if (disabled) return
        handleFiles(e.dataTransfer.files)
      }}
      onClick={() => !disabled && inputRef.current?.click()}
      role="button"
      tabIndex={0}
    >
      {disabled ? (
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      ) : (
        <Upload className="h-8 w-8 text-muted-foreground" />
      )}
      <p className="text-sm font-medium">
        {disabled ? '处理中，请勿关闭页面...' : '拖文件到这里 或 点击选择'}
      </p>
      <p className="text-xs text-muted-foreground">
        支持 {ACCEPT_EXTENSIONS.join(' / ')}（单文件 ≤ 10 MB）
      </p>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT_EXTENSIONS.join(',')}
        className="hidden"
        disabled={disabled}
        onChange={(e) => {
          handleFiles(e.target.files)
          // 重置 input，下次选同文件也能触发 onChange
          e.target.value = ''
        }}
      />
    </div>
  )
}
