/** Decorative sidebar showing the 8 agents. */
import { Mic, Brain, Map, Globe, Download, Wrench, Activity, Bell } from 'lucide-react'

const AGENTS = [
  { n: 1, label: 'Speech',       sub: 'Whisper / Sarvam',  Icon: Mic,      color: 'text-violet-400' },
  { n: 2, label: 'Intent',       sub: 'Mistral API',       Icon: Brain,    color: 'text-violet-400' },
  { n: 3, label: 'Planner',      sub: 'LangGraph',         Icon: Map,      color: 'text-teal-400'   },
  { n: 4, label: 'Browser',      sub: 'Playwright',        Icon: Globe,    color: 'text-teal-400'   },
  { n: 5, label: 'Download',     sub: 'SHA-256 verify',    Icon: Download, color: 'text-teal-400'   },
  { n: 6, label: 'Install',      sub: 'winget/brew/apt',   Icon: Wrench,   color: 'text-orange-400' },
  { n: 7, label: 'Monitor',      sub: 'Redis pub/sub',     Icon: Activity, color: 'text-orange-400' },
  { n: 8, label: 'Notify',       sub: 'SSE / WebSocket',   Icon: Bell,     color: 'text-orange-400' },
]

export function AgentPipeline() {
  return (
    <div className="space-y-1">
      <p className="text-xs font-mono text-slate-500 uppercase tracking-wider mb-3">
        Agent pipeline
      </p>
      {AGENTS.map((a, i) => (
        <div key={a.n} className="relative">
          <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-surface-700 group transition-colors">
            <span className="text-[10px] font-mono text-slate-600 w-4 flex-shrink-0">{a.n}</span>
            <a.Icon size={14} className={`flex-shrink-0 ${a.color}`} />
            <div className="min-w-0">
              <p className="text-xs font-medium text-slate-300">{a.label}</p>
              <p className="text-[10px] text-slate-600 font-mono truncate">{a.sub}</p>
            </div>
          </div>
          {i < AGENTS.length - 1 && (
            <div className="absolute left-[22px] top-full w-px h-1 bg-surface-600" />
          )}
        </div>
      ))}
    </div>
  )
}
