import { useState, FormEvent } from 'react'
import { Send, Terminal } from 'lucide-react'

interface Props {
  onSubmit: (query: string) => void
  disabled?: boolean
}

const SUGGESTIONS = [
  'Install VS Code',
  'Download Python 3.12 for Windows',
  'Install Docker Desktop',
  'Install Postman',
  'Install Git',
  'VS Code install karo',
]

export function CommandInput({ onSubmit, disabled }: Props) {
  const [value, setValue] = useState('')

  const submit = (q: string) => {
    const trimmed = q.trim()
    if (!trimmed) return
    onSubmit(trimmed)
    setValue('')
  }

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    submit(value)
  }

  return (
    <div className="space-y-3">
      <form onSubmit={handleSubmit} className="flex gap-2">
        <div className="relative flex-1">
          <Terminal
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
          />
          <input
            className="input pl-9 font-mono text-sm"
            placeholder='e.g. "Install VS Code" or "Python download karo"'
            value={value}
            onChange={e => setValue(e.target.value)}
            disabled={disabled}
            autoComplete="off"
          />
        </div>
        <button
          type="submit"
          className="btn-primary flex items-center gap-2 px-5"
          disabled={disabled || !value.trim()}
        >
          <Send size={16} />
          Run
        </button>
      </form>

      {/* Quick suggestions */}
      <div className="flex flex-wrap gap-2">
        {SUGGESTIONS.map(s => (
          <button
            key={s}
            onClick={() => submit(s)}
            disabled={disabled}
            className="text-xs font-mono px-3 py-1 rounded-full bg-surface-700
                       text-slate-400 hover:text-brand-300 hover:bg-surface-600
                       border border-surface-500 hover:border-brand-500/50
                       transition-colors disabled:opacity-40"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}
