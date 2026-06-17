import { CheckCircle2, XCircle, Loader2, Download, Settings, Search, Mic } from 'lucide-react'
import { TaskStatusResponse } from '../api/client'

type TaskStatusKey = 'pending' | 'running' | 'downloading' | 'installing' | 'completed' | 'failed' | 'cancelled'

interface Props {
  task: TaskStatusResponse
  progress: number
  progressMessage: string
}

const STATUS_ICON: Record<TaskStatusKey, JSX.Element> = {
  pending:     <Loader2 size={18} className="animate-spin text-slate-400" />,
  running:     <Loader2 size={18} className="animate-spin text-brand-400" />,
  downloading: <Download size={18} className="text-amber-400 animate-bounce" />,
  installing:  <Settings size={18} className="text-brand-400 animate-spin-slow" />,
  completed:   <CheckCircle2 size={18} className="text-emerald-400" />,
  failed:      <XCircle size={18} className="text-red-400" />,
  cancelled:   <XCircle size={18} className="text-slate-400" />,
}

const STATUS_LABEL: Record<TaskStatusKey, string> = {
  pending:     'Queued',
  running:     'Processing',
  downloading: 'Downloading',
  installing:  'Installing',
  completed:   'Complete',
  failed:      'Failed',
  cancelled:   'Cancelled',
}

const STATUS_COLOR: Record<TaskStatusKey, string> = {
  pending:     'text-slate-400',
  running:     'text-brand-300',
  downloading: 'text-amber-300',
  installing:  'text-brand-300',
  completed:   'text-emerald-300',
  failed:      'text-red-300',
  cancelled:   'text-slate-400',
}

// Show pipeline steps
const PIPELINE = [
  { key: 'speech',   label: 'Speech',   Icon: Mic },
  { key: 'intent',   label: 'Intent',   Icon: Search },
  { key: 'browser',  label: 'Browse',   Icon: Search },
  { key: 'download', label: 'Download', Icon: Download },
  { key: 'install',  label: 'Install',  Icon: Settings },
]

function getStepIndex(step: string | null) {
  if (!step) return -1
  return PIPELINE.findIndex(p => step.includes(p.key))
}

export function TaskProgress({ task, progress, progressMessage }: Props) {
  const isDone = task.status === 'completed' || task.status === 'failed'
  const currentStepIdx = isDone
    ? (task.status === 'completed' ? PIPELINE.length : getStepIndex(task.current_step))
    : getStepIndex(task.current_step)

  const barPct = isDone
    ? (task.status === 'completed' ? 100 : progress)
    : Math.max(progress, 5)

  const barColor = task.status === 'failed'
    ? 'bg-red-500'
    : task.status === 'completed'
    ? 'bg-emerald-500'
    : 'bg-brand-500'

  return (
    <div className="card p-5 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {STATUS_ICON[task.status as TaskStatusKey]}
          <span className={`font-medium text-sm ${STATUS_COLOR[task.status as TaskStatusKey]}`}>
            {STATUS_LABEL[task.status as TaskStatusKey]}
          </span>
        </div>
        <span className="text-xs font-mono text-slate-500">
          {task.task_id.slice(0, 8)}…
        </span>
      </div>

      {/* Query */}
      {task.result?.speech
        ? <p className="text-sm text-slate-300 font-mono bg-surface-700 rounded-lg px-3 py-2 truncate">
            "{String(task.result.speech?.query ?? 'voice command')}"
          </p>
        : null}

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-xs text-slate-500 mb-1.5">
          <span className="font-mono">{progressMessage || task.current_step || '…'}</span>
          <span className="font-mono">{barPct}%</span>
        </div>
        <div className="h-1.5 bg-surface-600 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${barColor}`}
            style={{ width: `${barPct}%` }}
          />
        </div>
      </div>

      {/* Pipeline steps */}
      <div className="flex items-center gap-1">
        {PIPELINE.map(({ key, label, Icon }, i) => {
          const done    = task.status === 'completed' || i < currentStepIdx
          const active  = i === currentStepIdx && !isDone
          const failed  = task.status === 'failed' && i === currentStepIdx
          return (
            <div key={key} className="flex items-center gap-1 flex-1 min-w-0">
              <div className={`
                flex flex-col items-center gap-1 flex-1 min-w-0
              `}>
                <div className={`
                  w-7 h-7 rounded-full flex items-center justify-center text-xs
                  ${failed  ? 'bg-red-500/20 text-red-400 border border-red-500/40' :
                    done    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' :
                    active  ? 'bg-brand-500/20 text-brand-400 border border-brand-500/40' :
                              'bg-surface-600 text-slate-600 border border-surface-500'}
                `}>
                  {done && !failed
                    ? <CheckCircle2 size={14} />
                    : <Icon size={14} className={active ? 'animate-spin' : ''} />
                  }
                </div>
                <span className={`text-[10px] font-mono truncate w-full text-center
                  ${done ? 'text-emerald-500' : active ? 'text-brand-400' : 'text-slate-600'}`}>
                  {label}
                </span>
              </div>
              {i < PIPELINE.length - 1 && (
                <div className={`h-px flex-1 mb-4 ${i < currentStepIdx ? 'bg-emerald-500/40' : 'bg-surface-500'}`} />
              )}
            </div>
          )
        })}
      </div>

      {/* Result details */}
      {task.status === 'completed' && task.result?.install
        ? <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-sm text-emerald-300">
            <p className="font-medium mb-1">Installation successful</p>
            <p className="text-xs text-emerald-400/70 font-mono">
              Method: {task.result.install?.install_method ?? 'N/A'}
            </p>
          </div>
        : null}

      {task.status === 'failed' && task.error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-sm text-red-300">
          <p className="font-medium mb-1">Installation failed</p>
          <p className="text-xs text-red-400/70 font-mono break-all">{task.error}</p>
        </div>
      )}
    </div>
  )
}
