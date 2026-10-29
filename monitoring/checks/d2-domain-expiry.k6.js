// D2 Domain expiry: Grafana Synthetic Monitoring, Scripted check, daily, 1 location.
// Reads the .com registry (Verisign RDAP), not the registrar, so it sees the date that
// actually counts. Fails when rayhanpatel.com has under 45 days left: check auto-renew
// and the payment method at the registrar (Amazon Registrar, i.e. Route 53 Domains).
import http from 'k6/http'
import { check, fail } from 'k6'

const DOMAIN = 'rayhanpatel.com'
const MIN_DAYS = 45

function must(ok, what) {
    if (!check(null, { [what]: () => ok })) fail(what)
}

export default function () {
    const res = http.get(`https://rdap.verisign.com/com/v1/domain/${DOMAIN}`,
        { headers: { Accept: 'application/rdap+json' }, timeout: '30s' })
    must(res.status === 200, `the registry answers (got ${res.status})`)
    let events = []
    try { events = res.json().events || [] } catch (e) { /* asserted below */ }
    const expiry = events.find((e) => e.eventAction === 'expiration')
    must(expiry, 'the registry lists an expiration date')
    const days = Math.floor((Date.parse(expiry.eventDate) - Date.now()) / 86400000)
    must(days >= MIN_DAYS, `${DOMAIN} has ${days} days left (expires ${expiry.eventDate.slice(0, 10)})`)
}
