import React from 'react'
import ReactDOM from 'react-dom/client'
import { Analytics } from "@vercel/analytics/react"
import { SpeedInsights } from "@vercel/speed-insights/react"
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import { initTelemetry } from './lib/telemetry.js'
import './index.css'

// Automated browsers are not visitors, so they skip visit counts and Faro sessions. The
// Grafana B1 check (k6 browser, every 15 minutes) does not set navigator.webdriver; its user
// agent ends in "GrafanaSyntheticMonitoring" and k6 exposes window.k6.
const automated = typeof navigator !== 'undefined' && (
    navigator.webdriver === true ||
    /GrafanaSyntheticMonitoring|HeadlessChrome/.test(navigator.userAgent) ||
    (typeof window !== 'undefined' && 'k6' in window))

// Vercel's analytics get the same treatment as Faro: the URL without its query (it can hold
// the visitor's name, company and first question)
const withoutQuery = (event) => (event && event.url ? { ...event, url: event.url.split(/[?#]/)[0] } : event)

if (!automated) initTelemetry()  // Grafana Faro, on chat.rayhanpatel.com only (src/lib/telemetry.js)

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <ErrorBoundary>
            <App />
            {!automated && <Analytics beforeSend={withoutQuery} />}
            {!automated && <SpeedInsights beforeSend={withoutQuery} />}
        </ErrorBoundary>
    </React.StrictMode>,
)
