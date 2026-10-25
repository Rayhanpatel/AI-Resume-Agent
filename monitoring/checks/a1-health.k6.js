// A1 Health: Grafana Synthetic Monitoring, Scripted check, every 5 min, 1 location.
// /health/llm makes a real model call (cached 10 min server-side, so this can't run up
// the bill) and a real database query. Passes only when both answer.
import http from 'k6/http'
import { check, fail } from 'k6'

const API = 'https://rayhan-ai-resume-production-6239.up.railway.app/api/v1'

function must(ok, what) {
    if (!check(null, { [what]: () => ok })) fail(what)
}

export default function () {
    const res = http.get(`${API}/health/llm`, { headers: { 'X-Synthetic-Check': 'A1' }, timeout: '60s' })
    let body = {}
    try { body = res.json() } catch (e) { /* asserted below */ }
    must(res.status === 200, `/health/llm answers 200 (got ${res.status}: ${String(res.body).slice(0, 200)})`)
    must(body.status === 'ok', `model answers (status: ${body.status}, model: ${body.model})`)
    must(body.database === 'ok', `database answers (database: ${body.database})`)
}
