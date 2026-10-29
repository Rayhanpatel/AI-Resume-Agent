// The front door to Faro. Faro loads lazily, on chat.rayhanpatel.com only (or wherever
// VITE_FARO_URL is set at build time); events that happen before it arrives are queued
// (up to 50), and elsewhere (local runs, preview deployments) they are dropped.
// The collector URL is public by design: it accepts data only from the CORS origin set
// on the Grafana Frontend Observability app "chat" (https://chat.rayhanpatel.com).
const PRODUCTION_FARO_URL = 'https://faro-collector-prod-us-west-0.grafana.net/collect/c0561b80da97a9558645faf4afe9d6c0'
const FARO_URL = import.meta.env.VITE_FARO_URL ||
    (typeof window !== 'undefined' && window.location.hostname === 'chat.rayhanpatel.com' ? PRODUCTION_FARO_URL : undefined)
const MAX_QUEUED = 50
let faro = null
const queue = []

export function initTelemetry() {
    if (!FARO_URL) return
    import('./faro.js')
        .then(({ startFaro }) => {
            faro = startFaro(FARO_URL)
            queue.splice(0).forEach((send) => send(faro))
        })
        .catch(() => { /* monitoring must never break the chat */ })
}

function withFaro(send) {
    if (!FARO_URL) return
    if (faro) send(faro)
    else if (queue.length < MAX_QUEUED) queue.push(send)
}

// Turnstile codes that mean the widget itself is misconfigured (bad site key, domain not
// allowed), as opposed to a visitor failing a challenge. Oct 2026: 400020 blocked every chat.
const CONFIG_ERROR = /^(110100|110110|110200|4000\d\d)$/

export function isTurnstileConfigError(code) {
    return CONFIG_ERROR.test(String(code))
}

// Render crashes caught by ErrorBoundary never reach window.onerror in a production React
// build, so it reports them here.
export function reportError(error, component) {
    withFaro((f) => f.api.pushError(error instanceof Error ? error : new Error(String(error)), {
        context: { component: String(component || 'unknown') },
    }))
}

export function reportTurnstileError(code) {
    const attributes = { code: String(code), config_error: String(isTurnstileConfigError(code)) }
    withFaro((f) => {
        f.api.pushEvent('turnstile_error', attributes)
        f.api.pushError(new Error(`Turnstile error ${code}`), { context: attributes })
    })
}
