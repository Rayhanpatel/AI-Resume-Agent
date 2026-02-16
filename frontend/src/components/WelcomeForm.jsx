import { useState, useEffect, useRef, useCallback } from 'react'
import { User, Building2, ArrowRight, Sparkles, Briefcase, Link } from 'lucide-react'

// Use test key in development (always passes), production key in production
const TURNSTILE_SITE_KEY = import.meta.env.DEV
    ? '1x00000000000000000000AA'
    : '0x4AAAAAAACa21R_BfLUd4767'

/**
 * WelcomeForm Component
 * 
 * Initial form where users enter their name, company, and optionally
 * a job description (text or URL) before starting the chat.
 * 
 * Includes Cloudflare Turnstile CAPTCHA (managed mode — invisible to most users).
 */
function WelcomeForm({ onStart, defaultValues = {} }) {
    const [name, setName] = useState(defaultValues.name || '')
    const [company, setCompany] = useState(defaultValues.company || '')
    const [jobInput, setJobInput] = useState(defaultValues.job || '')
    const [isSubmitting, setIsSubmitting] = useState(false)

    // Turnstile state
    const turnstileRef = useRef(null)
    const widgetIdRef = useRef(null)
    const tokenRef = useRef(null)
    const [turnstileReady, setTurnstileReady] = useState(false)

    // Render Turnstile widget when API is loaded
    useEffect(() => {
        let mounted = true

        const renderWidget = () => {
            if (!mounted || !turnstileRef.current || !window.turnstile) return
            // Don't double-render
            if (widgetIdRef.current !== null) return

            widgetIdRef.current = window.turnstile.render(turnstileRef.current, {
                sitekey: TURNSTILE_SITE_KEY,
                callback: (token) => {
                    tokenRef.current = token
                    if (mounted) setTurnstileReady(true)
                },
                'expired-callback': () => {
                    tokenRef.current = null
                    if (mounted) setTurnstileReady(false)
                },
                'error-callback': (errorCode) => {
                    tokenRef.current = null
                    console.warn('Turnstile error:', errorCode, '— enabling fallback in 3s')
                    // CRITICAL: Don't permanently lock the button on Turnstile failure.
                    // Allow submission after 3s — backend validates token and fails open
                    // if Cloudflare is unreachable (see session.py line 53-54).
                    setTimeout(() => {
                        if (mounted) setTurnstileReady(true)
                    }, 3000)
                },
                theme: 'dark',
                size: 'flexible',
            })
        }

        // Check if Turnstile API is already loaded
        if (window.turnstile) {
            renderWidget()
        } else {
            // Wait for script to load
            const interval = setInterval(() => {
                if (window.turnstile) {
                    clearInterval(interval)
                    renderWidget()
                }
            }, 100)
            // Cleanup interval after 10s
            const timeout = setTimeout(() => {
                clearInterval(interval)
                // If Turnstile never loads, allow form submission anyway
                if (mounted && !tokenRef.current) {
                    setTurnstileReady(true)
                }
            }, 10000)
            return () => {
                mounted = false
                clearInterval(interval)
                clearTimeout(timeout)
            }
        }

        return () => { mounted = false }
    }, [])

    const handleSubmit = useCallback(async (e) => {
        e.preventDefault()
        if (!name.trim()) return

        setIsSubmitting(true)
        // Pass turnstile token (may be null if Turnstile didn't load — backend handles gracefully)
        await onStart(name.trim(), company.trim(), jobInput.trim(), tokenRef.current)
    }, [name, company, jobInput, onStart])

    // Check if input looks like a URL
    const isUrl = jobInput.trim().toLowerCase().startsWith('http')

    return (
        <div className="glass-card rounded-2xl p-5 sm:p-8 animate-fade-in max-w-md w-full shadow-2xl max-h-[100dvh] overflow-y-auto">
            {/* Header */}
            <div className="text-center mb-4 sm:mb-8">
                <div className="inline-flex items-center justify-center w-14 h-14 sm:w-20 sm:h-20 rounded-2xl bg-gradient-to-br from-violet-500 to-cyan-500 mb-4 sm:mb-5 shadow-xl shadow-violet-500/30">
                    <Sparkles className="w-8 h-8 sm:w-10 sm:h-10 text-white" />
                </div>
                <h1 className="text-xl sm:text-2xl md:text-3xl font-bold text-white mb-2 sm:mb-3">
                    Chat with <span className="gradient-text">Rayhan's AI</span>
                </h1>
                <p className="text-gray-400 text-sm md:text-base leading-relaxed">
                    Ask me anything about Rayhan Patel's professional background,
                    skills, and experience!
                </p>
            </div>

            {/* Form */}
            <form onSubmit={handleSubmit} className="space-y-5">
                {/* Name Input */}
                <div>
                    <label
                        htmlFor="name"
                        className="block text-sm font-medium text-gray-300 mb-2"
                    >
                        Your Name <span className="text-primary-400">*</span>
                    </label>
                    <div className="relative group">
                        <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                            <User className="h-5 w-5 text-gray-500 group-focus-within:text-primary-400 transition-colors" />
                        </div>
                        <input
                            type="text"
                            id="name"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder="Enter your name"
                            required
                            className="w-full pl-12 pr-4 py-3.5 bg-white/5 border border-white/10 rounded-xl 
                             text-white placeholder-gray-500 focus:border-primary-500 focus:ring-2 
                             focus:ring-primary-500/20 transition-all duration-200 input-glow"
                        />
                    </div>
                </div>

                {/* Company Input */}
                <div>
                    <label
                        htmlFor="company"
                        className="block text-sm font-medium text-gray-300 mb-2"
                    >
                        Company <span className="text-gray-500">(Optional)</span>
                    </label>
                    <div className="relative group">
                        <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                            <Building2 className="h-5 w-5 text-gray-500 group-focus-within:text-primary-400 transition-colors" />
                        </div>
                        <input
                            type="text"
                            id="company"
                            value={company}
                            onChange={(e) => setCompany(e.target.value)}
                            placeholder="Enter your company name"
                            className="w-full pl-12 pr-4 py-3.5 bg-white/5 border border-white/10 rounded-xl 
                             text-white placeholder-gray-500 focus:border-primary-500 focus:ring-2 
                             focus:ring-primary-500/20 transition-all duration-200 input-glow"
                        />
                    </div>
                </div>

                {/* Job Posting Input - Accepts both URL and text */}
                <div>
                    <label
                        htmlFor="jobInput"
                        className="block text-sm font-medium text-gray-300 mb-2"
                    >
                        Job Posting <span className="text-gray-500">(Optional - paste URL or text)</span>
                    </label>
                    <div className="relative group">
                        <div className="absolute top-3.5 left-0 pl-4 flex items-start pointer-events-none">
                            {isUrl ? (
                                <Link className="h-5 w-5 text-cyan-400 transition-colors" />
                            ) : (
                                <Briefcase className="h-5 w-5 text-gray-500 group-focus-within:text-primary-400 transition-colors" />
                            )}
                        </div>
                        <textarea
                            id="jobInput"
                            value={jobInput}
                            onChange={(e) => setJobInput(e.target.value)}
                            placeholder="Paste job URL (e.g., linkedin.com/jobs/...) or job description text..."
                            rows={2} // default to 2 rows on mobile
                            className="w-full pl-12 pr-4 py-3.5 bg-white/5 border border-white/10 rounded-xl 
                             text-white placeholder-gray-500 focus:border-primary-500 focus:ring-2 
                             focus:ring-primary-500/20 transition-all duration-200 input-glow resize-none
                             placeholder:text-ellipsis placeholder:overflow-hidden"
                        />
                    </div>
                    {isUrl && (
                        <p className="text-xs text-cyan-400 mt-1.5 flex items-center gap-1">
                            <Link className="w-3 h-3" />
                            URL detected - job details will be extracted automatically
                        </p>
                    )}
                </div>

                {/* Cloudflare Turnstile Widget */}
                <div ref={turnstileRef} className="flex justify-center" />

                {/* Submit Button */}
                <button
                    type="submit"
                    disabled={!name.trim() || isSubmitting || !turnstileReady}
                    className="w-full flex items-center justify-center gap-3 py-4 px-6 
                     bg-gradient-to-r from-violet-500 to-cyan-500 hover:from-violet-400 
                     hover:to-cyan-400 text-white font-semibold rounded-xl transition-all 
                     duration-300 disabled:opacity-50 disabled:cursor-not-allowed btn-glow
                     transform hover:scale-[1.02] active:scale-[0.98] shadow-lg shadow-violet-500/30"
                >
                    {isSubmitting ? (
                        <>
                            <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                            {isUrl ? 'Extracting job details...' : 'Starting...'}
                        </>
                    ) : (
                        <>
                            Start Chatting
                            <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
                        </>
                    )}
                </button>
            </form>

            {/* Footer */}
            <div className="flex items-center justify-center gap-2 mt-6 pt-6 border-t border-white/10">
                <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                <p className="text-gray-500 text-xs">
                    Built by Rayhan Patel
                </p>
            </div>
        </div>
    )
}

export default WelcomeForm
