import { useRef, useState, useCallback } from 'react'

export interface RecordingResult {
  audioBase64: string
  durationMs: number
}

export function useVoiceRecorder() {
  const mediaRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const startTimeRef = useRef<number>(0)
  const [isRecording, setIsRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = useCallback(async () => {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      chunksRef.current = []
      startTimeRef.current = Date.now()

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      mediaRef.current = recorder
      recorder.start(100) // collect every 100ms
      setIsRecording(true)
    } catch (err) {
      setError('Microphone access denied. Please allow microphone permissions.')
      console.error('Recording error:', err)
    }
  }, [])

  const stop = useCallback((): Promise<RecordingResult> => {
    return new Promise((resolve, reject) => {
      const recorder = mediaRef.current
      if (!recorder) { reject(new Error('No recorder')); return }

      recorder.onstop = async () => {
        const durationMs = Date.now() - startTimeRef.current
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        const buf = await blob.arrayBuffer()
        const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)))
        recorder.stream.getTracks().forEach(t => t.stop())
        setIsRecording(false)
        resolve({ audioBase64: b64, durationMs })
      }

      recorder.stop()
    })
  }, [])

  return { start, stop, isRecording, error }
}
