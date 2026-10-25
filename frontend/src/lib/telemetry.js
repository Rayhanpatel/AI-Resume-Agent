// The front door to Faro. Faro loads lazily and only when VITE_FARO_URL is set; events
// that happen before it arrives are queued (up to 50), and without it they are dropped.
const FARO_URL = import.meta.env.VITE_FARO_URL
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

export function reportTurnstileError(code) {
    const attributes = { code: String(code), config_error: String(isTurnstileConfigError(code)) }
    withFaro((f) => {
        f.api.pushEvent('turnstile_error', attributes)
        f.api.pushError(new Error(`Turnstile error ${code}`), { context: attributes })
    })
}
