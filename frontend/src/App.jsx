import { useMemo, useEffect, useRef } from 'react'
import WelcomeForm from './components/WelcomeForm'
import ChatInterface from './components/ChatInterface'
import ResumeSessionModal from './components/ResumeSessionModal'
import { useChatSession } from './hooks/useChatSession'

/**
 * App Component
 * 
 * Thin UI wrapper. All business logic lives in useChatSession hook.
 * Reads URL params for QR-code deep-linking: ?company=...&name=...&job=...
 * and ?q=... (a question from rayhanpatel.com, asked once the chat starts).
 */
function App() {
    const {
        userName,
        company,
        messages,
        isLoading,
        error,
        hasStarted,
        suggestedPrompts,
        showResumeModal,
        pendingSession,
        handleStart,
        sendMessage,
        handleReset,
        handleContinueSession,
        handleStartFresh,
    } = useChatSession()

    // Read URL params for QR-code deep-link pre-fill
    const urlDefaults = useMemo(() => {
        const params = new URLSearchParams(window.location.search)
        return {
            name: params.get('name') || '',
            company: params.get('company') || '',
            job: params.get('job') || '',
            question: (params.get('q') || '').slice(0, 300),
        }
    }, [])

    // Ask the question passed from rayhanpatel.com once, right after the chat starts
    const pendingQuestion = useRef(urlDefaults.question)
    useEffect(() => {
        if (hasStarted && pendingQuestion.current) {
            const q = pendingQuestion.current
            pendingQuestion.current = ''
            sendMessage(q)
        }
    }, [hasStarted, sendMessage])

    return (
        <div className="h-[100dvh] w-full flex items-center justify-center p-0 sm:p-4 bg-black pb-[env(safe-area-inset-bottom)]">
            {/* Session Resume Modal */}
            {showResumeModal && pendingSession && (
                <ResumeSessionModal
                    userName={pendingSession.userName}
                    messageCount={pendingSession.messages?.length || 0}
                    onContinue={handleContinueSession}
                    onStartFresh={handleStartFresh}
                />
            )}

            {!hasStarted ? (
                <WelcomeForm onStart={handleStart} defaultValues={urlDefaults} />
            ) : (
                <div className="w-full max-w-2xl">
                    <ChatInterface
                        userName={userName}
                        company={company}
                        messages={messages}
                        isLoading={isLoading}
                        error={error}
                        onSendMessage={sendMessage}
                        onReset={handleReset}
                        suggestedPrompts={suggestedPrompts}
                    />
                </div>
            )}
        </div>
    )
}

export default App
