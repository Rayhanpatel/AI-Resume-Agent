// Grafana Faro (frontend observability): JavaScript errors, Turnstile failures and page
// performance, sent to the Faro collector URL. Imported only when VITE_FARO_URL is set,
// so the default bundle doesn't carry it. Console capture is off: nothing a visitor
// types is sent.
import { initializeFaro, getWebInstrumentations } from '@grafana/faro-web-sdk'

export function startFaro(url) {
    return initializeFaro({
        url,
        app: {
            name: 'chat',
            version: (import.meta.env.VITE_VERCEL_GIT_COMMIT_SHA || 'local').slice(0, 7),
            environment: import.meta.env.MODE,
        },
        instrumentations: getWebInstrumentations({ captureConsole: false }),
        preventGlobalExposure: true,
    })
}
