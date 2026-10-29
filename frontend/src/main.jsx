import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from "@vercel/analytics/react"
import { SpeedInsights } from "@vercel/speed-insights/react"
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import { initTelemetry } from './lib/telemetry.js'
import './index.css'

// Automated browsers (the Grafana B1 check runs headless Chrome every 15 minutes) set
// navigator.webdriver; they are not visitors, so they skip visit counts and Faro sessions.
const automated = typeof navigator !== 'undefined' && navigator.webdriver === true

if (!automated) initTelemetry()  // Grafana Faro, on chat.rayhanpatel.com only (src/lib/telemetry.js)

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <ErrorBoundary>
            <App />
            {!automated && <Analytics />}
            {!automated && <SpeedInsights />}
        </ErrorBoundary>
    </React.StrictMode>,
)
