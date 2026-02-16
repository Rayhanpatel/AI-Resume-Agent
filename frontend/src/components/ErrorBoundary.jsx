import { Component } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

/**
 * ErrorBoundary Component
 * 
 * Catches JavaScript errors in child component tree and displays
 * a fallback UI instead of crashing the whole app.
 */
class ErrorBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = { hasError: false, error: null }
    }

    static getDerivedStateFromError(error) {
        return { hasError: true, error }
    }

    componentDidCatch(error, errorInfo) {
        console.error('ErrorBoundary caught an error:', error, errorInfo)
    }

    handleReset = () => {
        this.setState({ hasError: false, error: null })
        // Clear any stored session data
        localStorage.removeItem('resumeChatSession')
        window.location.reload()
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="h-[100dvh] w-full flex items-center justify-center p-4 bg-black">
                    <div className="glass-card rounded-2xl p-8 max-w-md w-full text-center">
                        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-red-500/20 mb-5">
                            <AlertTriangle className="w-8 h-8 text-red-400" />
                        </div>

                        <h2 className="text-xl font-bold text-white mb-3">
                            Something went wrong
                        </h2>

                        <p className="text-gray-400 text-sm mb-6">
                            The application encountered an unexpected error.
                            Please try refreshing the page.
                        </p>

                        <button
                            onClick={this.handleReset}
                            aria-label="Refresh page to recover from error"
                            className="flex items-center justify-center gap-2 mx-auto py-3 px-6 
                             bg-gradient-to-r from-violet-500 to-cyan-500 hover:from-violet-400 
                             hover:to-cyan-400 text-white font-semibold rounded-xl transition-all 
                             duration-300 transform hover:scale-[1.02] active:scale-[0.98]"
                        >
                            <RefreshCw className="w-4 h-4" />
                            Refresh Page
                        </button>

                        {import.meta.env.DEV && this.state.error && (
                            <details className="mt-6 text-left">
                                <summary className="text-gray-500 text-xs cursor-pointer">
                                    Error details
                                </summary>
                                <pre className="mt-2 p-3 bg-white/5 rounded-lg text-xs text-red-400 overflow-auto">
                                    {this.state.error.toString()}
                                </pre>
                            </details>
                        )}
                    </div>
                </div>
            )
        }

        return this.props.children
    }
}

export default ErrorBoundary
