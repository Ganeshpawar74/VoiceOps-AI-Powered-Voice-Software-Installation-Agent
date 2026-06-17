import { useState } from 'react'
import { Mic, Type } from 'lucide-react'
import { VoiceMic } from '../components/VoiceMic'
import { CommandInput } from '../components/CommandInput'
import { TaskProgress } from '../components/TaskProgress'
import { useStore } from '../store'
import { useVoiceRecorder } from '../hooks/useVoiceRecorder'
import { useTaskStream } from '../hooks/useTaskStream'
import { api } from '../api/client'

type InputMode = 'text' | 'voice'

export function CommandPage() {
  const [mode, setMode] = useState<InputMode>('text')
  const {
    userId, activeTask, setActiveTask, setActiveTaskId,
    activeTaskId, progress, progressMessage,
    isSubmitting, setIsSubmitting,
    pushNotification, prependHistory,
  } = useStore()

  const recorder = useVoiceRecorder()
  useTaskStream(activeTaskId)

  const handleTextSubmit = async (query: string) => {
    setIsSubmitting(true)
    try {
      const res = await api.submitText(userId, query)
      setActiveTaskId(res.task_id)
      prependHistory({ task_id: res.task_id, query, status: 'pending', created_at: new Date().toISOString() })
      // Get initial task state
      const task = await api.getTask(res.task_id)
      setActiveTask(task)
    } catch (err: unknown) {
      pushNotification((err as Error).message || 'Failed to submit command', 'error')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleVoiceStart = () => recorder.start()

  const handleVoiceStop = async () => {
    setIsSubmitting(true)
    try {
      const { audioBase64 } = await recorder.stop()
      const res = await api.submitVoice(userId, audioBase64)
      setActiveTaskId(res.task_id)
      prependHistory({ task_id: res.task_id, query: '(voice command)', status: 'pending', created_at: new Date().toISOString() })
      const task = await api.getTask(res.task_id)
      setActiveTask(task)
    } catch (err: unknown) {
      pushNotification((err as Error).message || 'Voice submission failed', 'error')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Mode toggle */}
      <div className="flex gap-1 p-1 bg-surface-800 rounded-xl border border-surface-600 w-fit">
        {([['text', 'Text command', Type], ['voice', 'Voice command', Mic]] as const).map(([m, label, Icon]) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all
              ${mode === m
                ? 'bg-brand-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'}`}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>

      {/* Input area */}
      <div className="card p-6">
        {mode === 'text' ? (
          <CommandInput onSubmit={handleTextSubmit} disabled={isSubmitting} />
        ) : (
          <div className="flex flex-col items-center py-4">
            <VoiceMic
              isRecording={recorder.isRecording}
              onStart={handleVoiceStart}
              onStop={handleVoiceStop}
              disabled={isSubmitting}
            />
            {recorder.error && (
              <p className="mt-3 text-sm text-red-400">{recorder.error}</p>
            )}
            <p className="mt-4 text-xs text-slate-600 text-center max-w-xs">
              Supports English, Hindi, and Hinglish.<br />
              "Install VS Code" · "Python install karo" · "Docker chahiye"
            </p>
          </div>
        )}
      </div>

      {/* Task progress */}
      {activeTask && (
        <TaskProgress
          task={activeTask}
          progress={progress}
          progressMessage={progressMessage}
        />
      )}

      {/* Empty state */}
      {!activeTask && (
        <div className="card p-8 text-center">
          <div className="w-12 h-12 rounded-xl bg-brand-600/20 border border-brand-500/30
                          flex items-center justify-center mx-auto mb-4">
            <Mic size={22} className="text-brand-400" />
          </div>
          <h3 className="text-slate-300 font-medium mb-1">Ready to install</h3>
          <p className="text-slate-500 text-sm">
            Type or speak a command like<br />
            <span className="font-mono text-brand-400">"Install VS Code"</span>
          </p>
        </div>
      )}
    </div>
  )
}
