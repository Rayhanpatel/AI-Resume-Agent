// S1 Site: Grafana Synthetic Monitoring, Scripted check, every 15 min, 1 location.
// rayhanpatel.com and the resume load. Asserts content, not just a 200: the home page names
// him and has the chat launcher; the resume is a real PDF; profile.json is valid JSON.
import http from 'k6/http'
import { check, fail } from 'k6'

const SITE = 'https://rayhanpatel.com'

function must(ok, what) {
    if (!check(null, { [what]: () => ok })) fail(what)
}

export default function () {
    const home = http.get(`${SITE}/`, { headers: { 'X-Synthetic-Check': 'S1' }, timeout: '30s' })
    must(home.status === 200, `home page answers 200 (got ${home.status})`)
    must(String(home.body).includes('Rayhan Patel'), 'home page names Rayhan Patel')
    must(String(home.body).includes('id="launcher"'), 'home page has the chat launcher')

    const pdf = http.get(`${SITE}/Rayhan_Patel_Resume.pdf`, { responseType: 'binary', timeout: '30s' })
    must(pdf.status === 200, `resume answers 200 (got ${pdf.status})`)
    const head = new Uint8Array(pdf.body.slice(0, 4))
    must(String.fromCharCode(...head) === '%PDF', 'resume is a PDF')
    must(pdf.body.byteLength > 20000, `resume is not empty (${pdf.body.byteLength} bytes)`)

    const profile = http.get(`${SITE}/profile.json`, { timeout: '30s' })
    let ok = false
    try { ok = !!profile.json().meta } catch (e) { /* asserted below */ }
    must(profile.status === 200 && ok, `profile.json is valid (got ${profile.status})`)
}
