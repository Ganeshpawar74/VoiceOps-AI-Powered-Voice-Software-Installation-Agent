import { create } from 'zustand'
import { TaskStatusResponse, HistoryTask } from '../api/client'

interface Notification {
  id: string
  message: string
  type: 'success' | 'error' | 'info'
}

interface VoiceOpsStore {
  // Auth (demo mode — replace with real auth)
  userId: string

  // Active task
  activeTaskId: string | null
  activeTask: TaskStatusResponse | null
  setActiveTask: (t: TaskStatusResponse | null) => void
  setActiveTaskId: (id: string | null) => void

  // History
  history: HistoryTask[]
  setHistory: (h: HistoryTask[]) => void
  prependHistory: (t: HistoryTask) => void

  // Progress from SSE
  progress: number
  progressMessage: string
  setProgress: (pct: number, msg: string) => void

  // Notifications (toast)
  notifications: Notification[]
  pushNotification: (msg: string, type?: Notification['type']) => void
  dismissNotification: (id: string) => void

  // UI state
  isRecording: boolean
  setIsRecording: (v: boolean) => void
  isSubmitting: boolean
  setIsSubmitting: (v: boolean) => void
  activeTab: 'command' | 'history' | 'docs'
  setActiveTab: (t: VoiceOpsStore['activeTab']) => void
}

export const useStore = create<VoiceOpsStore>((set, get) => ({
  userId: 'demo-user',

  activeTaskId: null,
  activeTask: null,
  setActiveTask: (t) => set({ activeTask: t }),
  setActiveTaskId: (id) => set({ activeTaskId: id, progress: 0, progressMessage: '' }),

  history: [],
  setHistory: (h) => set({ history: h }),
  prependHistory: (t) => set({ history: [t, ...get().history].slice(0, 50) }),

  progress: 0,
  progressMessage: '',
  setProgress: (pct, msg) => set({ progress: pct, progressMessage: msg }),

  notifications: [],
  pushNotification: (message, type = 'info') => {
    const id = Math.random().toString(36).slice(2)
    set(s => ({ notifications: [...s.notifications, { id, message, type }] }))
    setTimeout(() => get().dismissNotification(id), 5000)
  },
  dismissNotification: (id) =>
    set(s => ({ notifications: s.notifications.filter(n => n.id !== id) })),

  isRecording: false,
  setIsRecording: (v) => set({ isRecording: v }),
  isSubmitting: false,
  setIsSubmitting: (v) => set({ isSubmitting: v }),
  activeTab: 'command',
  setActiveTab: (t) => set({ activeTab: t }),
}))
