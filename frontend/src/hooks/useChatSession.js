import { useState, useCallback, useEffect, useRef } from 'react'

const SESSION_TTL_MS = 24 * 60 * 60 * 1000 // 24 hours
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/**
 * useChatSession Hook
 * 
 * Encapsulates all chat business logic:
 * - User state (name, company, job posting)
 * - Chat state (messages, loading, errors)
 * - Session persistence (localStorage with 24h TTL)
 * - Session resume modal state
 * - Streaming message handling
 * - Keyboard shortcuts
 */
export function useChatSession() {
    // User state
    const [userName, setUserName] = useState('')
    const [company, setCompany] = useState('')
    const [jobPosting, setJobPosting] = useState('')
    const [sessionId, setSessionId] = useState('')
    const [sessionToken, setSessionToken] = useState('')
    const [hasStarted, setHasStarted] = useState(false)
    const [suggestedPrompts, setSuggestedPrompts] = useState(null)

    // Chat state
    const [messages, setMessages] = useState([])
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState(null)

    // Refs to avoid stale closures and enable cleanup
    const isLoadingRef = useRef(false)
    const streamAbortRef = useRef(null)

    // Session resume modal state
    const [showResumeModal, setShowResumeModal] = useState(false)
    const [pendingSession, setPendingSession] = useState(null)

    // ── Session Persistence ──────────────────────────────────────────

    // Check for existing session on mount and show modal
    useEffect(() => {
        try {
            const saved = localStorage.getItem('resumeChatSession')
            if (saved) {
                const session = JSON.parse(saved)
                // Check expiration
                if (session.savedAt && Date.now() - session.savedAt > SESSION_TTL_MS) {
                    localStorage.removeItem('resumeChatSession')
                    return
                }
                if (session.userName && session.messages?.length > 0) {
                    setPendingSession(session)
                    setShowResumeModal(true)
                }
            }
        } catch (e) {
            console.error('Failed to restore session:', e)
        }
    }, [])

    // Save session to localStorage when it changes
    useEffect(() => {
        if (hasStarted && messages.length > 0) {
            localStorage.setItem('resumeChatSession', JSON.stringify({
                userName,
                company,
                jobPosting,
                sessionId,
                sessionToken,
                suggestedPrompts,
                messages,
                savedAt: Date.now()
            }))
        }
    }, [hasStarted, userName, company, jobPosting, messages, sessionId, sessionToken, suggestedPrompts])

    // Clear session from localStorage
    const clearSession = useCallback(() => {
        localStorage.removeItem('resumeChatSession')
    }, [])

    // ── Session Resume Handlers ──────────────────────────────────────

    const handleContinueSession = useCallback(() => {
        if (pendingSession) {
            setUserName(pendingSession.userName)
            setCompany(pendingSession.company || '')
            setJobPosting(pendingSession.jobPosting || '')
            setMessages(pendingSession.messages)
            setSessionId(pendingSession.sessionId || '')
            setSessionToken(pendingSession.sessionToken || '')
            setSuggestedPrompts(pendingSession.suggestedPrompts || null)
            setHasStarted(true)
        }
        setShowResumeModal(false)
        setPendingSession(null)
    }, [pendingSession])

    const handleStartFresh = useCallback(() => {
        clearSession()
        setShowResumeModal(false)
        setPendingSession(null)
    }, [clearSession])

    // ── Keyboard Shortcuts ───────────────────────────────────────────

    const handleReset = useCallback(() => {
        streamAbortRef.current?.abort()
        setHasStarted(false)
        setMessages([])
        setUserName('')
        setCompany('')
        setJobPosting('')
        setSessionId('')
        setSessionToken('')
        setSuggestedPrompts(null)
        setError(null)
        clearSession()
    }, [clearSession])

    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === 'Escape') {
                if (showResumeModal) {
                    handleStartFresh()
                } else if (hasStarted) {
                    handleReset()
                }
            }
        }
        window.addEventListener('keydown', handleKeyDown)
        return () => window.removeEventListener('keydown', handleKeyDown)
    }, [hasStarted, showResumeModal, handleStartFresh, handleReset])

    // ── Chat Actions ─────────────────────────────────────────────────

    /**
     * Handle form submission from WelcomeForm.
     * Creates a backend session and initializes chat with welcome message.
     */
    const handleStart = useCallback(async (name, companyName, jobInput, turnstileToken) => {
        setUserName(name)
        setCompany(companyName)
        setJobPosting(jobInput)

        let sid = ''
        let token = ''
        let welcome = ''
        let prompts = null

        try {
            const controller = new AbortController()
            const timeout = setTimeout(() => controller.abort(), 30000)

            const isUrl = jobInput?.trim().toLowerCase().startsWith('http')

            const response = await fetch(`${API_URL}/api/v1/session`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_name: name,
                    company: companyName || null,
                    job_posting: isUrl ? null : (jobInput || null),
                    job_url: isUrl ? jobInput : null,
                    turnstile_token: turnstileToken || null
                }),
                signal: controller.signal
            })
            clearTimeout(timeout)

            if (response.ok) {
                const data = await response.json()
                sid = data.session_id
                token = data.session_token
                welcome = data.welcome_message
                prompts = data.suggested_prompts || null

                if (data.extraction_error) {
                    console.warn('Job extraction warning:', data.extraction_error)
                }
            } else {
                const errorData = await response.json().catch(() => ({}))
                // The API wraps structured errors as {error, code, details}
                const detail = errorData.details ?? errorData.error ?? errorData.detail
                welcome = typeof detail === 'object' ? (detail.error || detail.message) : detail
            }
        } catch (err) {
            console.error('Session creation failed:', err)
        }

        if (!sid || !token) {
            // No session means no chat: the backend issues the token every message needs
            return { ok: false, message: welcome || 'The agent is unavailable right now. Please try again in a minute, or email rayhanbp@umd.edu.' }
        }

        setSessionId(sid)
        setSessionToken(token)
        setSuggestedPrompts(prompts)
        setMessages([{
            id: Date.now(),
            role: 'assistant',
            content: welcome
        }])
        setHasStarted(true)
        return { ok: true }
    }, [])

    /**
     * Send a message and stream the AI response via SSE.
     */
    const sendMessage = useCallback(async (messageText) => {
        if (!messageText.trim() || isLoadingRef.current) return

        setError(null)

        const userMessage = {
            id: Date.now(),
            role: 'user',
            content: messageText
        }
        setMessages(prev => [...prev, userMessage])

        const aiMessageId = Date.now() + 1
        setMessages(prev => [...prev, { id: aiMessageId, role: 'assistant', content: '' }])
        setIsLoading(true)
        isLoadingRef.current = true
        streamAbortRef.current = new AbortController()

        try {
            const response = await fetch(`${API_URL}/api/v1/chat/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // Name, company, job and history live in the server-side session
                body: JSON.stringify({
                    query: messageText,
                    session_id: sessionId,
                    session_token: sessionToken
                }),
                signal: streamAbortRef.current.signal
            })

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}))
                const detail = errorData.details ?? errorData.error ?? errorData.detail
                const message = typeof detail === 'object'
                    ? (detail.message || detail.error || JSON.stringify(detail))
                    : (detail || `HTTP error! status: ${response.status}`)
                const httpErr = new Error(message)
                httpErr.status = response.status
                throw httpErr
            }

            const reader = response.body.getReader()
            const decoder = new TextDecoder()

            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                const text = decoder.decode(value)
                const lines = text.split('\n').filter(line => line.startsWith('data: '))

                for (const line of lines) {
                    const data = line.slice(6)
                    if (data === '[DONE]') continue

                    try {
                        const parsed = JSON.parse(data)
                        if (parsed.chunk) {
                            setMessages(prev => prev.map(msg =>
                                msg.id === aiMessageId
                                    ? { ...msg, content: msg.content + parsed.chunk }
                                    : msg
                            ))
                        } else if (parsed.status === 'verifying') {
                            // The answer is complete; the backend is checking it against the profile
                            setMessages(prev => prev.map(msg =>
                                msg.id === aiMessageId ? { ...msg, verifying: true } : msg
                            ))
                        } else if (parsed.verification) {
                            setMessages(prev => prev.map(msg =>
                                msg.id === aiMessageId ? { ...msg, verifying: false, verification: parsed.verification } : msg
                            ))
                        }
                    } catch {
                        // Ignore parse errors for partial chunks
                    }
                }
            }

        } catch (err) {
            if (err.name === 'AbortError') return  // User cancelled — not an error
            console.error('Stream error:', err)

            if (err.status === 429) {
                setError(err.message && !err.message.startsWith('HTTP') ? err.message : 'Too many requests. Please wait a minute before sending another message.')
            } else if (err.status === 403) {
                setError('This chat session has expired. Press Escape or Reset to start a new one.')
            } else {
                setError(err.message || 'Failed to send message. Please try again.')
            }

            setMessages(prev => prev.map(msg =>
                msg.id === aiMessageId
                    ? { ...msg, content: "I apologize, but I'm having trouble connecting right now. Please try again in a moment." }
                    : msg
            ))
        } finally {
            setMessages(prev => prev.map(msg => msg.id === aiMessageId && msg.verifying ? { ...msg, verifying: false } : msg))
            setIsLoading(false)
            isLoadingRef.current = false
        }
    }, [sessionId, sessionToken])

    // ── Return Public API ────────────────────────────────────────────

    return {
        // User info
        userName,
        company,

        // Chat state
        messages,
        isLoading,
        error,
        hasStarted,
        suggestedPrompts,

        // Session resume
        showResumeModal,
        pendingSession,

        // Actions
        handleStart,
        sendMessage,
        handleReset,
        handleContinueSession,
        handleStartFresh,
    }
}
