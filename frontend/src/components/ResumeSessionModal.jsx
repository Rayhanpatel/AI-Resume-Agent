import { X, MessageSquare, ArrowRight } from 'lucide-react'

/**
 * ResumeSessionModal Component
 * 
 * Shows when a user returns with an existing session (< 24h old).
 * Offers choice to continue previous conversation or start fresh.
 */
function ResumeSessionModal({ userName, messageCount, onContinue, onStartFresh }) {
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in"
            role="dialog"
            aria-modal="true"
            aria-labelledby="resume-modal-title"
        >
            <div className="glass-card rounded-2xl p-6 sm:p-8 max-w-sm w-full shadow-2xl">
                {/* Header */}
                <div className="text-center mb-6">
                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-gradient-to-br from-violet-500 to-cyan-500 mb-4 shadow-lg">
                        <MessageSquare className="w-7 h-7 text-white" />
                    </div>
                    <h2 id="resume-modal-title" className="text-xl font-bold text-white mb-2">
                        Welcome back, {userName}!
                    </h2>
                    <p className="text-gray-400 text-sm">
                        You have a conversation with {messageCount} messages. Would you like to continue?
                    </p>
                </div>

                {/* Actions */}
                <div className="space-y-3">
                    <button
                        onClick={onContinue}
                        className="w-full flex items-center justify-center gap-2 py-3 px-4 
                         bg-gradient-to-r from-violet-500 to-cyan-500 hover:from-violet-400 
                         hover:to-cyan-400 text-white font-semibold rounded-xl transition-all 
                         duration-300 transform hover:scale-[1.02] active:scale-[0.98] btn-glow"
                        aria-label="Continue previous chat session"
                    >
                        Continue Chat
                        <ArrowRight className="w-4 h-4" />
                    </button>

                    <button
                        onClick={onStartFresh}
                        className="w-full flex items-center justify-center gap-2 py-3 px-4 
                         bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20
                         text-gray-300 hover:text-white font-medium rounded-xl transition-all duration-200"
                        aria-label="Clear session and start a new chat"
                    >
                        Start Fresh
                        <X className="w-4 h-4" />
                    </button>
                </div>
            </div>
        </div>
    )
}

export default ResumeSessionModal
