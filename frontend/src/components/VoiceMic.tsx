import { Mic, MicOff, Square } from 'lucide-react'
import { useStore } from '../store'

interface Props {
  isRecording: boolean
  onStart: () => void
  onStop: () => void
  disabled?: boolean
}

export function VoiceMic({ isRecording, onStart, onStop, disabled }: Props) {
  return (
    <div className="flex flex-col items-center gap-4">
      <button
        onClick={isRecording ? onStop : onStart}
        disabled={disabled}
        className={`
          relative w-20 h-20 rounded-full flex items-center justify-center
          transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed
          ${isRecording
            ? 'bg-red-500/20 border-2 border-red-400 text-red-400 hover:bg-red-500/30'
            : 'bg-brand-600/20 border-2 border-brand-500 text-brand-400 hover:bg-brand-600/30'
          }
        `}
        aria-label={isRecording ? 'Stop recording' : 'Start recording'}
      >
        {/* Pulse rings when recording */}
        {isRecording && (
          <>
            <span className="absolute inset-0 rounded-full border border-red-400/40 animate-ping" />
            <span className="absolute inset-[-8px] rounded-full border border-red-400/20 animate-ping [animation-delay:0.3s]" />
          </>
        )}
        {isRecording ? <Square size={28} fill="currentColor" /> : <Mic size={28} />}
      </button>

      {/* Waveform bars */}
      {isRecording && (
        <div className="flex items-center gap-1 h-8">
          {Array.from({ length: 8 }).map((_, i) => (
            <div
              key={i}
              className="wave-bar w-1 h-6 bg-red-400 rounded-full origin-bottom"
            />
          ))}
        </div>
      )}

      <p className="text-xs text-slate-500 font-mono">
        {isRecording ? 'Recording… click to stop' : 'Click to speak'}
      </p>
    </div>
  )
}
