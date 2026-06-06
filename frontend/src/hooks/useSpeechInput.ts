import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

// Web Speech API 没有进 TS 标准 lib，这里声明用到的最小子集
type SpeechRecognitionResult = {
  0: { transcript: string }
  isFinal: boolean
}
type SpeechRecognitionEvent = {
  resultIndex: number
  results: { length: number } & Record<number, SpeechRecognitionResult>
}
type SpeechRecognitionErrorEvent = { error: string }
type SpeechRecognitionLike = {
  lang: string
  continuous: boolean
  interimResults: boolean
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((e: SpeechRecognitionEvent) => void) | null
  onerror: ((e: SpeechRecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  onstart: (() => void) | null
  onaudiostart: (() => void) | null
  onspeechstart: (() => void) | null
  onspeechend: (() => void) | null
  onaudioend: (() => void) | null
}

// 把 Web Speech API 的错误码翻成给用户看的提示。
// 注：该 API 不能在代码里指定麦克风，只用系统/浏览器默认输入设备 ——
// 插耳机后默认录音设备没切到耳机，多半报 audio-capture 或采不到声音报 no-speech。
function errorHint(code: string): string | null {
  switch (code) {
    case 'aborted':
      return null // 用户主动停止，不提示
    case 'not-allowed':
    case 'service-not-allowed':
      return '麦克风权限被拒绝，请在浏览器地址栏的站点权限里允许'
    case 'audio-capture':
      return '采集不到麦克风：把耳机设为系统默认录音设备，再点 Chrome 地址栏麦克风图标选对设备'
    case 'no-speech':
      return '没听到声音：可能默认输入设备不是耳机，检查系统录音设备'
    case 'network':
      return '语音识别网络错误，请重试'
    default:
      return `语音识别出错（${code}）`
  }
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike

function getCtor(): SpeechRecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/**
 * 浏览器原生语音听写（零后端）。识别到的最终片段通过 onText 回调追加。
 * 不支持的浏览器 supported=false，调用方据此隐藏麦克风按钮。
 */
export function useSpeechInput(onText: (text: string) => void) {
  const [supported] = useState(() => getCtor() !== null)
  const [listening, setListening] = useState(false)
  const recRef = useRef<SpeechRecognitionLike | null>(null)
  const onTextRef = useRef(onText)
  useEffect(() => {
    onTextRef.current = onText
  }, [onText])

  const stop = useCallback(() => {
    recRef.current?.stop()
    setListening(false)
  }, [])

  const start = useCallback(() => {
    const Ctor = getCtor()
    if (!Ctor) return
    const rec = new Ctor()
    rec.lang = 'zh-CN'
    rec.continuous = true
    rec.interimResults = false
    // 诊断日志：开 DevTools 控制台（F12 → Console）能看到完整生命周期，
    // 用来判断耳机场景到底是没采到音(audiostart 不触发)还是采到了没识别出来。
    rec.onstart = () => console.debug('[speech] start')
    rec.onaudiostart = () => console.debug('[speech] audiostart（开始采集音频）')
    rec.onspeechstart = () => console.debug('[speech] speechstart（检测到说话）')
    rec.onspeechend = () => console.debug('[speech] speechend')
    rec.onaudioend = () => console.debug('[speech] audioend')
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]
        console.debug('[speech] result', { isFinal: r.isFinal, transcript: r[0].transcript })
        if (r.isFinal) onTextRef.current(r[0].transcript)
      }
    }
    rec.onerror = (e) => {
      console.warn('[speech] error:', e.error)
      const hint = errorHint(e.error)
      if (hint) toast.error(hint)
      setListening(false)
    }
    rec.onend = () => {
      console.debug('[speech] end')
      setListening(false)
    }
    recRef.current = rec
    try {
      rec.start()
      setListening(true)
    } catch {
      setListening(false)
    }
  }, [])

  const toggle = useCallback(() => {
    if (listening) stop()
    else start()
  }, [listening, start, stop])

  useEffect(() => () => recRef.current?.abort(), [])

  return { supported, listening, toggle }
}
