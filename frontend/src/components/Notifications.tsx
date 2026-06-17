import { CheckCircle2, XCircle, Info, X } from 'lucide-react'
import { useStore } from '../store'

export function Notifications() {
  const { notifications, dismissNotification } = useStore()

  if (notifications.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full">
      {notifications.map(n => (
        <div
          key={n.id}
          className={`
            card px-4 py-3 flex items-start gap-3 shadow-2xl
            animate-in slide-in-from-right duration-200
            ${n.type === 'success' ? 'border-emerald-500/30' :
              n.type === 'error'   ? 'border-red-500/30' :
                                     'border-brand-500/30'}
          `}
        >
          <div className="flex-shrink-0 mt-0.5">
            {n.type === 'success' ? <CheckCircle2 size={16} className="text-emerald-400" /> :
             n.type === 'error'   ? <XCircle size={16} className="text-red-400" /> :
                                    <Info size={16} className="text-brand-400" />}
          </div>
          <p className="flex-1 text-sm text-slate-200 leading-snug">{n.message}</p>
          <button
            onClick={() => dismissNotification(n.id)}
            className="flex-shrink-0 text-slate-500 hover:text-slate-300 transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}
