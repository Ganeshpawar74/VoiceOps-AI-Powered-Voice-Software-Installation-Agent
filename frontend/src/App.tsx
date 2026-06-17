import { useEffect, useState } from 'react'
import { Mic, History, BookOpen, Cpu, Wifi, WifiOff } from 'lucide-react'
import { CommandPage } from './pages/CommandPage'
import { HistoryPanel } from './components/HistoryPanel'
import { DocsPage } from './pages/DocsPage'
import { AgentPipeline } from './components/AgentPipeline'
import { Notifications } from './components/Notifications'
import { useStore } from './store'
import { api } from './api/client'

type Tab = 'command' | 'history' | 'docs'

const TABS: { id: Tab; label: string; Icon: React.ElementType }[] = [
  { id: 'command', label: 'Command',  Icon: Mic      },
  { id: 'history', label: 'History',  Icon: History  },
  { id: 'docs',    label: 'Docs',     Icon: BookOpen },
]

function useApiHealth() {
  const [online, setOnline] = useState<boolean | null>(null)
  useEffect(() => {
    api.health()
      .then(() => setOnline(true))
      .catch(() => setOnline(false))
    const t = setInterval(() => {
      api.health().then(() => setOnline(true)).catch(() => setOnline(false))
    }, 30_000)
    return () => clearInterval(t)
  }, [])
  return online
}

export default function App() {
  const [tab, setTab] = useState<Tab>('command')
  const online = useApiHealth()

  return (
    <div className="min-h-screen flex flex-col" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* Top bar */}
      <header className="border-b border-surface-700 bg-surface-900/80 backdrop-blur sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-brand-600 flex items-center justify-center">
              <Mic size={14} className="text-white" />
            </div>
            <span className="font-semibold text-slate-100 tracking-tight">VoiceOps</span>
            <span className="text-[10px] font-mono text-slate-600 hidden sm:block ml-1">
              AI Install Agent
            </span>
          </div>

          <div className="flex items-center gap-3">
            {/* API status */}
            <div className="flex items-center gap-1.5 text-xs font-mono">
              {online === null ? (
                <span className="text-slate-600">checking…</span>
              ) : online ? (
                <>
                  <Wifi size={12} className="text-emerald-400" />
                  <span className="text-emerald-400/70">API online</span>
                </>
              ) : (
                <>
                  <WifiOff size={12} className="text-red-400" />
                  <span className="text-red-400/70">API offline</span>
                </>
              )}
            </div>

            {/* Agent count badge */}
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full
                            bg-surface-700 border border-surface-600">
              <Cpu size={11} className="text-brand-400" />
              <span className="text-[10px] font-mono text-slate-400">8 agents</span>
            </div>
          </div>
        </div>
      </header>

      {/* Main layout */}
      <div className="flex-1 max-w-6xl mx-auto w-full px-4 py-6 flex gap-6">
        {/* Left sidebar */}
        <aside className="hidden lg:block w-48 flex-shrink-0">
          <AgentPipeline />
        </aside>

        {/* Content */}
        <main className="flex-1 min-w-0">
          {/* Tab nav */}
          <div className="flex gap-1 mb-6 border-b border-surface-700">
            {TABS.map(({ id, label, Icon }) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium
                            transition-colors border-b-2 -mb-px
                            ${tab === id
                              ? 'border-brand-500 text-brand-300'
                              : 'border-transparent text-slate-500 hover:text-slate-300'}`}
              >
                <Icon size={15} />
                {label}
              </button>
            ))}
          </div>

          {tab === 'command' && <CommandPage />}
          {tab === 'history' && <HistoryPanel />}
          {tab === 'docs'    && <DocsPage />}
        </main>

        {/* Right sidebar — stack info */}
        <aside className="hidden xl:block w-52 flex-shrink-0 space-y-4">
          <div className="card p-4 space-y-2">
            <p className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Stack</p>
            {[
              ['STT',    'Whisper / Sarvam'],
              ['LLM',    'Mistral API'],
              ['Graph',  'LangGraph'],
              ['Browser','Playwright'],
              ['Queue',  'Celery + Redis'],
              ['DB',     'PostgreSQL'],
              ['RAG',    'Qdrant'],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between text-xs">
                <span className="text-slate-600 font-mono">{k}</span>
                <span className="text-slate-400 font-mono">{v}</span>
              </div>
            ))}
          </div>

          <div className="card p-4 space-y-2">
            <p className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">APIs</p>
            <a href="/api/docs" target="_blank"
               className="block text-xs text-brand-400 hover:text-brand-300 font-mono transition-colors">
              /api/docs →
            </a>
            <a href="/api/health" target="_blank"
               className="block text-xs text-brand-400 hover:text-brand-300 font-mono transition-colors">
              /api/health →
            </a>
          </div>
        </aside>
      </div>

      <Notifications />
    </div>
  )
}
