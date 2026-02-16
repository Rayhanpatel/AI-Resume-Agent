import { useState, useEffect, memo } from 'react'
import { Sun, Moon } from 'lucide-react'

/**
 * ThemeToggle Component
 * 
 * Toggles between dark and light mode.
 * Priority: localStorage saved preference > OS system preference > dark default.
 */
function ThemeToggle() {
    const [isDark, setIsDark] = useState(() => {
        const saved = localStorage.getItem('theme')
        if (saved) return saved === 'dark'
        // Respect OS system preference, fall back to dark
        return !window.matchMedia('(prefers-color-scheme: light)').matches
    })

    useEffect(() => {
        document.documentElement.classList.toggle('light-mode', !isDark)
        localStorage.setItem('theme', isDark ? 'dark' : 'light')
    }, [isDark])

    return (
        <button
            onClick={() => setIsDark(!isDark)}
            className="p-2.5 sm:p-2 rounded-lg hover:bg-white/10 transition-colors min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0 flex items-center justify-center"
            title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
        >
            {isDark ? (
                <Sun className="w-5 h-5 text-gray-400 hover:text-white transition-colors" />
            ) : (
                <Moon className="w-5 h-5 text-gray-600 hover:text-gray-900 transition-colors" />
            )}
        </button>
    )
}

export default memo(ThemeToggle)
