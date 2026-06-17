import { useEffect, useRef, useCallback } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'

export function useTaskStream(taskId: string | null) {
  const sourceRef = useRef<EventSource | null>(null)
  const { setProgress, setActiveTask, pushNotification } = useStore()

  const close = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  useEffect(() => {
    if (!taskId) return
    close()

    const es = api.streamTask(taskId, () => {})
    sourceRef.current = es

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.event === 'progress') {
          setProgress(data.data?.progress_pct ?? 0, data.message)
        }
        if (data.event === 'install_status') {
          const isOk = data.data?.status === 'completed'
          pushNotification(data.message, isOk ? 'success' : 'error')
          if (isOk || data.data?.status === 'failed') {
            // Fetch final task state
            api.getTask(taskId).then(setActiveTask).catch(() => {})
            close()
          }
        }
      } catch { /* ignore parse errors */ }
    }

    es.onerror = () => {
      close()
    }

    // Poll as backup (every 4s)
    const poll = setInterval(() => {
      api.getTask(taskId).then(task => {
        setActiveTask(task)
        if (task.progress_pct) setProgress(task.progress_pct, task.current_step || '')
        if (task.status === 'completed' || task.status === 'failed') {
          clearInterval(poll)
          close()
        }
      }).catch(() => {})
    }, 4000)

    return () => {
      clearInterval(poll)
      close()
    }
  }, [taskId])
}
