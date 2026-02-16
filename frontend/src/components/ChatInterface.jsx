import { useState, useRef, useEffect, memo } from 'react'
import { Send, RotateCcw, Bot, User, AlertCircle, Linkedin, Mail, Github, Globe, Copy, Check, Download, Share2, Code } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import QuickQuestions from './QuickQuestions'
import ThemeToggle from './ThemeToggle'

/**
 * ChatInterface Component
 * 
 * Main chat UI that displays messages and handles user input.
 * Includes chat history, typing indicator, quick questions, and message input.
 */
function ChatInterface({
    userName,
    company,
    messages,
    isLoading,
    error,
    onSendMessage,
    onReset,
    suggestedPrompts  // NEW: LLM-generated prompts from session
}) {
    const [inputValue, setInputValue] = useState('')
    const [copiedId, setCopiedId] = useState(null)
    const [chatCopied, setChatCopied] = useState(false)
    const messagesEndRef = useRef(null)
    const inputRef = useRef(null)

    /**
     * Share chat - copy full conversation to clipboard
     */
    const shareChat = async () => {
        const text = messages.map(m =>
            `${m.role === 'user' ? userName : "Rayhan's AI"}: ${m.content}`
        ).join('\n\n---\n\n')

        const fullText = `Chat with Rayhan's AI\n${'='.repeat(30)}\n\n${text}\n\n---\nTry it: https://chat.rayhanpatel.com`

        try {
            await navigator.clipboard.writeText(fullText)
            setChatCopied(true)
            setTimeout(() => setChatCopied(false), 2000)
        } catch (err) {
            console.error('Failed to copy chat:', err)
        }
    }

    /**
     * Copy message content to clipboard
     */
    const copyToClipboard = async (content, id) => {
        try {
            await navigator.clipboard.writeText(content)
            setCopiedId(id)
            setTimeout(() => setCopiedId(null), 2000)
        } catch (err) {
            console.error('Failed to copy:', err)
        }
    }

    // Auto-scroll to bottom when new messages arrive
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages, isLoading])

    // Focus input on mount
    useEffect(() => {
        inputRef.current?.focus()
    }, [])

    // Keyboard shortcuts
    useEffect(() => {
        const handleKeyDown = (e) => {
            // Cmd/Ctrl + K to focus input
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault()
                inputRef.current?.focus()
            }
        }
        window.addEventListener('keydown', handleKeyDown)
        return () => window.removeEventListener('keydown', handleKeyDown)
    }, [])

    // Format timestamp
    const formatTime = (timestamp) => {
        return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }

    const handleSubmit = (e) => {
        e.preventDefault()
        if (!inputValue.trim() || isLoading) return

        onSendMessage(inputValue)
        setInputValue('')
    }

    const handleQuickQuestion = (question) => {
        if (isLoading) return
        onSendMessage(question)
    }

    return (
        <div className="w-full max-w-3xl mx-auto h-full sm:h-[90vh] flex flex-col glass-card rounded-none sm:rounded-2xl border-x-0 border-y-0 sm:border overflow-hidden animate-fade-in">
            {/* Header */}
            <div className="glass px-3 py-2 sm:px-4 sm:py-3 border-b border-white/10 shrink-0">
                <div className="flex items-center justify-between">
                    {/* User Info */}
                    <div className="flex items-center gap-2 sm:gap-3 overflow-hidden">
                        <div className="w-8 h-8 sm:w-10 sm:h-10 rounded-full bg-gradient-to-br from-primary-500 to-purple-600 flex items-center justify-center shadow-lg shrink-0">
                            <Bot className="w-4 h-4 sm:w-5 sm:h-5 text-white" />
                        </div>
                        <div className="min-w-0">
                            <h2 className="text-white font-semibold text-sm sm:text-base truncate">Rayhan's AI</h2>
                            <p className="text-gray-400 text-[10px] sm:text-xs truncate">
                                w/ {userName}
                            </p>
                        </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1 sm:gap-2">
                        {/* Contact Links — hidden on mobile, visible on sm+ */}
                        <a
                            href="https://www.linkedin.com/in/rayhan-patel-cs/"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hidden sm:flex p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-blue-400 transition-all hover:scale-110"
                            title="LinkedIn"
                            aria-label="Visit Rayhan's LinkedIn profile"
                        >
                            <Linkedin className="w-4 h-4" />
                        </a>
                        <a
                            href="mailto:rayhanbp@umd.edu"
                            className="hidden sm:flex p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-primary-400 transition-all hover:scale-110"
                            title="Email"
                            aria-label="Email Rayhan"
                        >
                            <Mail className="w-4 h-4" />
                        </a>
                        <a
                            href="https://github.com/Rayhanpatel"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hidden sm:flex p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-all hover:scale-110"
                            title="GitHub Profile"
                            aria-label="Visit Rayhan's GitHub profile"
                        >
                            <Github className="w-4 h-4" />
                        </a>
                        {/* Source Code Link */}
                        {import.meta.env.VITE_REPO_URL && (
                            <a
                                href={import.meta.env.VITE_REPO_URL}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="hidden sm:flex p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-yellow-400 transition-all hover:scale-110"
                                title="View Source Code"
                                aria-label="View source code"
                            >
                                <Code className="w-4 h-4" />
                            </a>
                        )}
                        <a
                            href="https://www.rayhanpatel.com"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hidden sm:flex p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-primary-400 transition-all hover:scale-110"
                            title="Portfolio"
                            aria-label="Visit Rayhan's portfolio website"
                        >
                            <Globe className="w-4 h-4" />
                        </a>
                        <a
                            href="/rayhanbp.pdf"
                            download
                            className="hidden sm:flex p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-green-400 transition-all hover:scale-110"
                            title="Download Resume"
                            aria-label="Download Rayhan's resume as PDF"
                        >
                            <Download className="w-4 h-4" />
                        </a>
                        <button
                            onClick={shareChat}
                            className="p-2.5 sm:p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-cyan-400 transition-all hover:scale-110 min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0 flex items-center justify-center"
                            title="Copy conversation"
                            aria-label={chatCopied ? 'Conversation copied' : 'Copy entire conversation to clipboard'}
                        >
                            {chatCopied ? (
                                <Check className="w-4 h-4 text-green-400" />
                            ) : (
                                <Share2 className="w-4 h-4" />
                            )}
                        </button>

                        {/* Divider */}
                        <div className="w-px h-6 bg-white/10 mx-1"></div>

                        {/* Theme Toggle */}
                        <ThemeToggle />

                        {/* Reset Button */}
                        <button
                            onClick={onReset}
                            className="flex items-center justify-center gap-1 px-3 py-1.5 text-sm text-gray-400 
                           hover:text-white hover:bg-white/10 rounded-lg transition-all hover:scale-105
                           min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0"
                            title="Start over (Esc)"
                            aria-label="Reset chat and start over"
                        >
                            <RotateCcw className="w-4 h-4" />
                            <span className="hidden sm:inline">Reset</span>
                        </button>
                    </div>
                </div>
            </div>

            {/* Messages Area */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4" role="log" aria-live="polite" aria-label="Chat messages">
                {messages.map((message) => (
                    <div
                        key={message.id}
                        className={`flex gap-3 message-enter ${message.role === 'user' ? 'flex-row-reverse' : ''}`}
                    >
                        {/* Avatar */}
                        <div className={`flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center shadow-lg ${message.role === 'user'
                            ? 'bg-gradient-to-br from-violet-500 to-cyan-500'
                            : 'bg-gradient-to-br from-violet-600 to-violet-800'
                            }`}>
                            {message.role === 'user' ? (
                                <User className="w-4 h-4 text-white" />
                            ) : (
                                <Bot className="w-4 h-4 text-white" />
                            )}
                        </div>

                        {/* Message Bubble */}
                        <div className={`message-bubble max-w-[85%] sm:max-w-[80%] px-4 py-3 rounded-2xl relative shadow-lg ${message.role === 'user'
                            ? 'bg-gradient-to-br from-violet-500 to-cyan-500 text-white rounded-tr-sm'
                            : 'glass-darker text-gray-100 rounded-tl-sm ai-message'
                            }`}>
                            <div className="text-sm md:text-base prose prose-invert prose-sm max-w-none">
                                {message.content ? (
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                        {message.content}
                                    </ReactMarkdown>
                                ) : (
                                    /* Typing cursor for empty streaming message */
                                    <span className="inline-flex gap-1">
                                        <span className="typing-dot w-2 h-2 bg-primary-400 rounded-full"></span>
                                        <span className="typing-dot w-2 h-2 bg-primary-400 rounded-full"></span>
                                        <span className="typing-dot w-2 h-2 bg-primary-400 rounded-full"></span>
                                    </span>
                                )}
                            </div>

                            {/* Copy & timestamp for AI messages - hide for empty streaming messages */}
                            {message.role === 'assistant' && message.content && (
                                <div className="flex items-center gap-2 mt-3 pt-2 border-t border-white/10">
                                    <button
                                        onClick={() => copyToClipboard(message.content, message.id)}
                                        className="p-1 rounded hover:bg-white/10 text-gray-500 hover:text-gray-300 transition-colors"
                                        title="Copy"
                                        aria-label={copiedId === message.id ? 'Message copied' : 'Copy message to clipboard'}
                                    >
                                        {copiedId === message.id ? (
                                            <Check className="w-3 h-3 text-green-400" />
                                        ) : (
                                            <Copy className="w-3 h-3" />
                                        )}
                                    </button>
                                    <span className="text-[10px] text-gray-500 ml-auto">
                                        {formatTime(message.id)}
                                    </span>
                                </div>
                            )}

                            {/* Timestamp for user messages */}
                            {message.role === 'user' && (
                                <span className="text-[10px] text-white/50 mt-1 block text-right">
                                    {formatTime(message.id)}
                                </span>
                            )}
                        </div>
                    </div>
                ))}

                {/* Loading Indicator - hide when streaming (last message is empty assistant) */}
                {isLoading && !(messages.length > 0 && messages[messages.length - 1]?.role === 'assistant' && messages[messages.length - 1]?.content === '') && (
                    <div className="flex gap-3 message-enter">
                        <div className="flex-shrink-0 w-9 h-9 rounded-full bg-gradient-to-br from-indigo-600 to-purple-700 flex items-center justify-center shadow-lg avatar-pulse">
                            <Bot className="w-4 h-4 text-white" />
                        </div>
                        <div className="glass-darker px-4 py-3 rounded-2xl rounded-tl-sm shadow-lg">
                            <div className="flex gap-1.5">
                                <span className="typing-dot w-2 h-2 bg-primary-400 rounded-full"></span>
                                <span className="typing-dot w-2 h-2 bg-primary-400 rounded-full"></span>
                                <span className="typing-dot w-2 h-2 bg-primary-400 rounded-full"></span>
                            </div>
                        </div>
                    </div>
                )}

                {/* Error Message */}
                {error && (
                    <div className="flex gap-3 message-enter">
                        <div className="flex-shrink-0 w-9 h-9 rounded-full bg-red-600/20 flex items-center justify-center">
                            <AlertCircle className="w-4 h-4 text-red-400" />
                        </div>
                        <div className="bg-red-600/10 border border-red-500/20 text-red-200 px-4 py-3 rounded-2xl rounded-tl-sm">
                            <p className="text-sm">{error}</p>
                        </div>
                    </div>
                )}

                <div ref={messagesEndRef} />
            </div>

            {/* Input Area */}
            <div className="glass p-3 sm:p-4 border-t border-white/10">
                {/* Quick Questions - only show when no messages yet */}
                {messages.length === 1 && (
                    <QuickQuestions
                        onSelectQuestion={handleQuickQuestion}
                        disabled={isLoading}
                        company={company}
                        customPrompts={suggestedPrompts}
                    />
                )}

                <form onSubmit={handleSubmit} className="flex gap-3">
                    <input
                        ref={inputRef}
                        type="text"
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        placeholder="Ask me about Rayhan's experience..."
                        disabled={isLoading}
                        className="flex-1 bg-white/5 border border-white/10 rounded-xl px-4 py-3 
                         text-white placeholder-gray-500 focus:border-primary-500 
                         focus:ring-2 focus:ring-primary-500/20 transition-all duration-200
                         disabled:opacity-50 input-glow"
                    />
                    <button
                        type="submit"
                        disabled={!inputValue.trim() || isLoading}
                        className="px-4 py-3 bg-gradient-to-r from-primary-600 to-purple-600 
                         hover:from-primary-500 hover:to-purple-500 text-white rounded-xl 
                         transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed
                         btn-glow shadow-lg"
                        aria-label="Send message"
                    >
                        <Send className="w-5 h-5" />
                    </button>
                </form>

                {/* Footer */}
                <p className="text-center text-gray-500 text-xs mt-3">
                    Built by Rayhan Patel
                </p>
            </div>
        </div>
    )
}

export default memo(ChatInterface)
