import { CheckCircle2, XCircle, Clock, RefreshCw } from 'lucide-react'
import { HistoryTask } from '../api/client'
import { useStore } from '../store'
import { api } from '../api/client'
import { useEffect } from 'react'

type TaskStatusKey = 'pending' | 'running' | 'downloading' | 'installing' | 'completed' | 'failed' | 'cancelled'

const STATUS_ICON: Record<TaskStatusKey, JSX.Element> = {
  pending:     <Clock size={14} className="text-slate-400" />,
  running:     <Clock size={14} className="text-brand-400" />,
  downloading: <Clock size={14} className="text-amber-400" />,
  installing:  <Clock size={14} className="text-brand-400" />,
  completed:   <CheckCircle2 size={14} className="text-emerald-400" />,
  failed:      <XCircle size={14} className="text-red-400" />,
  cancelled:   <XCircle size={14} className="text-slate-400" />,
}

function timeAgo(iso: string): string {
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60)  return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export function HistoryPanel() {
  const { history, setHistory, userId } = useStore()

  const load = async () => {
    try {
      const tasks = await api.listTasks()
      setHistory(tasks as unknown as HistoryTask[])
    } catch { /* backend may not be running */ }
  }

  useEffect(() => { load() }, [])

  if (history.length === 0) {
    return (
      <div className="card p-8 text-center">
        <Clock size={32} className="mx-auto mb-3 text-slate-600" />
        <p className="text-slate-500 text-sm">No installation history yet.</p>
        <p className="text-slate-600 text-xs mt-1">Run a command to get started.</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-slate-400">Recent installs</h2>
        <button onClick={load} className="btn-ghost flex items-center gap-1.5">
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {history.map(task => (
        <div
          key={task.task_id}
          className="card px-4 py-3 flex items-center gap-3 hover:border-surface-500 transition-colors"
        >
          <div className="flex-shrink-0">{STATUS_ICON[task.status as TaskStatusKey] ?? <Clock size={14} />}</div>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-slate-200 truncate font-mono">{task.query}</p>
            <p className="text-xs text-slate-500 mt-0.5">{timeAgo(task.created_at)}</p>
          </div>
          <span className={`badge text-xs font-mono flex-shrink-0
            ${task.status === 'completed' ? 'bg-emerald-500/15 text-emerald-400' :
              task.status === 'failed'    ? 'bg-red-500/15 text-red-400' :
                                           'bg-surface-600 text-slate-400'}`}>
            {task.status}
          </span>
        </div>
      ))}
    </div>
  )
}
