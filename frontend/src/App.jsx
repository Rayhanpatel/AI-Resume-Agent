import { useMemo } from 'react'
import WelcomeForm from './components/WelcomeForm'
import ChatInterface from './components/ChatInterface'
import ResumeSessionModal from './components/ResumeSessionModal'
import { useChatSession } from './hooks/useChatSession'

/**
 * App Component
 * 
 * Thin UI wrapper. All business logic lives in useChatSession hook.
 * Reads URL params for QR-code deep-linking: ?company=...&name=...&job=...
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
        }
    }, [])

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
