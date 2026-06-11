import {
  AlertCircle,
  Check,
  FileText,
  Globe,
  Loader2,
  Microscope,
  Search,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type {
  ResearchAction,
  ResearchPhase,
  ResearchState,
  ResearchSubagent,
} from '@/types/chat'

const PHASE_LABEL: Record<ResearchPhase, string> = {
  planning: '规划中…',
  researching: '并行检索中',
  reflecting: '反思中',
  synthesizing: '综述中…',
  done: '研究完成',
}

/** 深度研究进度面板：四阶段 + 子代理行（渲染在报告正文之上）。 */
export function ResearchPanel({ research }: { research: ResearchState }) {
  const { phase, subquestions, subagents, reflect } = research
  // 子代理已开跑就按子代理渲染；否则用规划出的子问题占位
  const rows: ResearchSubagent[] =
    subagents.length > 0
      ? subagents
      : subquestions.map((q) => ({
          sub_id: q.id,
          question: q.text,
          status: 'running' as const,
          sources: 0,
        }))

  return (
    <div className="rounded-2xl border border-border bg-muted/40 px-4 py-3 text-sm">
      <div className="mb-2 flex items-center gap-2 font-medium">
        <Microscope className="h-4 w-4 text-violet-500" />
        <span>深度研究</span>
        <span className="text-xs font-normal text-muted-foreground">· {PHASE_LABEL[phase]}</span>
      </div>

      {rows.length > 0 ? (
        <ul className="space-y-1.5">
          {rows.map((s) => (
            <li key={s.sub_id} className="flex items-start gap-2">
              <SubagentIcon status={s.status} />
              <div className="min-w-0 flex-1">
                <div className="break-words text-foreground">{s.question}</div>
                {s.actions && s.actions.length > 0 ? (
                  <ul className="mt-1 space-y-0.5">
                    {s.actions.map((a, i) => (
                      <ActionRow key={i} action={a} />
                    ))}
                  </ul>
                ) : null}
                <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                  {s.status === 'running' && s.label && !s.actions?.length ? (
                    <span>{s.label}…</span>
                  ) : null}
                  {s.sources > 0 ? <span>{s.sources} 条来源</span> : null}
                  {s.status === 'failed' ? (
                    <span className="text-destructive">{s.note || '未查到资料'}</span>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-xs text-muted-foreground">正在拆解研究问题…</div>
      )}

      {reflect && (reflect.gap || (reflect.followups && reflect.followups.length > 0)) ? (
        <div className="mt-2 border-t border-border/60 pt-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">反思：</span>
          {reflect.gap ? <span>{reflect.gap}</span> : <span>{reflect.note}</span>}
          {reflect.followups && reflect.followups.length > 0 ? (
            <span>（补查 {reflect.followups.length} 项）</span>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function ActionRow({ action }: { action: ResearchAction }) {
  const Icon =
    action.label.includes('网页') || action.label.includes('读取')
      ? FileText
      : action.label.includes('联网')
        ? Globe
        : Search
  return (
    <li className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
      {action.status === 'running' ? (
        <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-violet-400" />
      ) : action.status === 'error' ? (
        <AlertCircle className="mt-0.5 h-3 w-3 shrink-0 text-destructive" />
      ) : (
        <Icon className="mt-0.5 h-3 w-3 shrink-0" />
      )}
      <span className="min-w-0 break-words">
        <span className="text-foreground/70">{action.label}</span>
        {action.detail ? <span>：{action.detail}</span> : null}
        {action.status === 'empty' ? <span className="opacity-60">（无结果）</span> : null}
      </span>
    </li>
  )
}

function SubagentIcon({ status }: { status: ResearchSubagent['status'] }) {
  if (status === 'ok') {
    return <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
  }
  if (status === 'failed') {
    return <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
  }
  return (
    <Loader2
      className={cn('mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-violet-500')}
    />
  )
}
