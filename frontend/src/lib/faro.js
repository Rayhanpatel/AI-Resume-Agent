// Grafana Faro (frontend observability): JavaScript errors, Turnstile failures and page
// performance, sent to the Faro collector URL. Imported only when VITE_FARO_URL is set,
// so the default bundle doesn't carry it. Console capture is off: nothing a visitor
// types is sent.
import { initializeFaro, getWebInstrumentations } from '@grafana/faro-web-sdk'

// The chat page reads name, company, job and the first question from its URL (rayhanpatel.com
// links to ?q=...), so every URL Faro reports is cut at "?" or "#": no visitor text in Grafana.
export const withoutQuery = (value) =>
    (typeof value === 'string' && /^https?:\/\//.test(value) ? value.split(/[?#]/)[0] : value)

export function scrub(item) {
    if (item?.meta?.page?.url) item.meta.page.url = withoutQuery(item.meta.page.url)
    for (const bag of [item?.payload?.attributes, item?.payload?.context]) {
        if (bag) for (const key of Object.keys(bag)) bag[key] = withoutQuery(bag[key])
    }
    return item
}

export function startFaro(url) {
    return initializeFaro({
        url,
        app: {
            name: 'chat',
            version: (import.meta.env.VITE_VERCEL_GIT_COMMIT_SHA || 'local').slice(0, 7),
            environment: import.meta.env.VITE_VERCEL_ENV || import.meta.env.MODE,
        },
        instrumentations: getWebInstrumentations({ captureConsole: false }),
        preventGlobalExposure: true,
        beforeSend: scrub,
    })
}
