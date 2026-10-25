// B1 Chat page: Grafana Synthetic Monitoring, Browser check, every 15 min, 1 location.
// rayhanpatel.com and the resume PDF load; chat.rayhanpatel.com renders its form and the
// Turnstile widget has no configuration error. It never solves the challenge (Cloudflare
// doesn't support automated solving in production, and a probe doesn't need to): a
// challenge or a "bot" verdict for this headless browser is fine, a bad site key or an
// unauthorised domain is not. Oct 2026: error 400020 (invalid site key) blocked every chat
// for hours. Needs a frontend with data-turnstile-state (see WelcomeForm.jsx).
import { browser } from 'k6/browser'
import http from 'k6/http'
import { check, fail } from 'k6'

export const options = {
    scenarios: {
        ui: {
            executor: 'shared-iterations',
            options: { browser: { type: 'chromium' } },
        },
    },
}

const SITE = 'https://rayhanpatel.com/'
const RESUME = 'https://rayhanpatel.com/Rayhan_Patel_Resume.pdf'
const CHAT = 'https://chat.rayhanpatel.com/'
// Same list as frontend/src/lib/telemetry.js: a misconfigured widget, not a visitor challenge
const CONFIG_ERROR = /^error:(110100|110110|110200|4000\d\d)$/

function must(ok, what) {
    if (!check(null, { [what]: () => ok })) fail(what)
}

// null when the page has no marker (an old frontend); a locator would wait 30 s instead
async function turnstileState(page) {
    const el = await page.$('[data-turnstile-state]')
    return el ? el.getAttribute('data-turnstile-state') : null
}

export default async function () {
    const site = http.get(SITE, { headers: { 'X-Synthetic-Check': 'B1' } })
    must(site.status === 200, `rayhanpatel.com loads (HTTP ${site.status})`)
    const pdf = http.get(RESUME, { headers: { 'X-Synthetic-Check': 'B1' } })
    must(pdf.status === 200 && String(pdf.headers['Content-Type']).includes('pdf'),
        `resume PDF loads (HTTP ${pdf.status})`)

    const page = await browser.newPage()
    try {
        await page.goto(CHAT, { waitUntil: 'networkidle' })
        must(await page.locator('form').isVisible(), 'chat form renders')

        // The app waits up to 10 s for Turnstile; give it 12 to render and report
        let state = await turnstileState(page)
        must(state !== null, 'the page exposes data-turnstile-state (frontend 2.7.0 or later)')
        for (let i = 0; i < 24 && state === 'loading'; i++) {
            await page.waitForTimeout(500)
            state = await turnstileState(page)
        }
        await page.waitForTimeout(3000) // an error callback can follow the first render
        state = await turnstileState(page)

        must(state !== 'not-loaded' && state !== 'loading', `Turnstile loads (state: ${state})`)
        must(!CONFIG_ERROR.test(state), `Turnstile has no configuration error (state: ${state})`)
    } finally {
        await page.close()
    }
}
