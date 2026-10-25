import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from "@vercel/analytics/react"
import { SpeedInsights } from "@vercel/speed-insights/react"
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import { initTelemetry } from './lib/telemetry.js'
import './index.css'

initTelemetry()  // off unless VITE_FARO_URL is set at build time

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <ErrorBoundary>
            <App />
            <Analytics />
            <SpeedInsights />
        </ErrorBoundary>
    </React.StrictMode>,
)
