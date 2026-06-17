/**
 * VoiceOps — API Client
 *
 * FIX 1 (401 Unauthorized): Dev mode now sends X-User-ID header instead of
 *   a JWT Bearer token, matching the backend's get_current_user() dev fallback.
 *
 * FIX 2 (submitVoice/submitText not a function): Added method aliases that
 *   CommandPage.tsx actually calls:
 *     api.submitVoice(userId, audioBase64)  → POST /api/v1/voice/command
 *     api.submitText(userId, query)         → POST /api/v1/text/command
 *   The original voiceCommand/textCommand names are kept for backwards compat.
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? ''

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getToken(): string | null {
  return sessionStorage.getItem('voiceops_token')
}

export function saveToken(token: string): void {
  sessionStorage.setItem('voiceops_token', token)
}

export function clearToken(): void {
  sessionStorage.removeItem('voiceops_token')
}

function authHeaders(): Record<string, string> {
  const mode = import.meta.env.VITE_AUTH_MODE ?? 'dev'

  if (mode === 'prod') {
    const token = getToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  }

  // Dev mode: X-User-ID is accepted by the backend without any JWT
  return { 'X-User-ID': 'anonymous' }
}

// ── Core fetch wrapper ────────────────────────────────────────────────────────

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...authHeaders(),
    ...(options.headers as Record<string, string> | undefined ?? {}),
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers })

  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API ${res.status}: ${body}`)
  }

  if (res.status === 204) return null as unknown as T
  return res.json() as Promise<T>
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TaskResponse {
  task_id: string
  status: string
  message: string
}

export interface TaskStatus {
  task_id: string
  status: string
  progress_pct: number | null
  current_step: string | null
  result: Record<string, Record<string, string | undefined> | undefined> | null
  error: string | null
  created_at: string
  updated_at: string
}

export type TaskStatusResponse = TaskStatus

export interface HistoryTask {
  task_id: string
  query: string
  status: string
  created_at: string
}

// ── API surface ───────────────────────────────────────────────────────────────

export const api = {
  /** Health check — no auth required. */
  health(): Promise<{ status: string; version: string; auth_mode: string }> {
    return request('/api/health')
  },

  /**
   * Submit a voice command.
   * Called by CommandPage.tsx as: api.submitVoice(userId, audioBase64)
   */
  submitVoice(userId: string, audioBase64: string): Promise<TaskResponse> {
    return request('/api/v1/voice/command', {
      method: 'POST',
      body: JSON.stringify({
        audio_base64: audioBase64,
        user_id: userId,
      }),
    })
  },

  /**
   * Submit a plain-text command.
   * Called by CommandPage.tsx as: api.submitText(userId, query)
   */
  submitText(userId: string, query: string): Promise<TaskResponse> {
    return request('/api/v1/text/command', {
      method: 'POST',
      body: JSON.stringify({
        query,
        user_id: userId,
      }),
    })
  },

  /** Poll task status by ID. */
  getTask(taskId: string): Promise<TaskStatus> {
    return request(`/api/v1/tasks/${taskId}`)
  },

  /** List all tasks for the current user. */
  listTasks(): Promise<TaskStatus[]> {
    return request('/api/v1/tasks')
  },

  /**
   * Subscribe to Server-Sent Events for real-time task progress.
   * Returns an EventSource — caller must call .close() when done.
   */
  streamTask(
    taskId: string,
    onMessage: (data: unknown) => void,
    onError?: (e: Event) => void,
  ): EventSource {
    const es = new EventSource(`${BASE_URL}/api/v1/tasks/${taskId}/stream`)
    es.onmessage = (e) => {
      try { onMessage(JSON.parse(e.data)) } catch { onMessage(e.data) }
    }
    if (onError) es.onerror = onError
    return es
  },

  /** Production only — exchange credentials for a JWT. */
  login(username: string, password: string): Promise<{ access_token: string }> {
    return request('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  },

  // ── Backwards-compat aliases (in case other components use these names) ──
  voiceCommand: (payload: { audio_base64: string; user_id?: string; session_id?: string }) =>
    request<TaskResponse>('/api/v1/voice/command', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  textCommand: (payload: { query: string; user_id?: string; session_id?: string; os_hint?: string }) =>
    request<TaskResponse>('/api/v1/text/command', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}