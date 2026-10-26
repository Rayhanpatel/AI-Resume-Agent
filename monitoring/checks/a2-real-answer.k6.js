// A2 Real answer: Grafana Synthetic Monitoring, Scripted check, every 6 hours, 1 location.
// Asks the agent a fixed question through its MCP interface (no session or bot check
// needed; the same agent and verifier as the web chat) and checks the answer is right
// and verified: it names 3E, carries stamps, at least one sentence "checked", in under
// 15 s. One full answer an hour, about 0.4M model tokens a day.
import http from 'k6/http'
import { check, fail } from 'k6'

const MCP = 'https://rayhan-ai-resume-production-6239.up.railway.app/mcp/'
const QUESTION = 'Where did Rayhan do his summer 2026 internship, and what did he work on?'
// Protocol 2026-07-28 is stateless: no initialize; every request carries the version and
// client capabilities in params._meta, plus mcp-method / mcp-name headers.
const META = {
    'io.modelcontextprotocol/protocolVersion': '2026-07-28',
    'io.modelcontextprotocol/clientCapabilities': {},
}

function must(ok, what) {
    if (!check(null, { [what]: () => ok })) fail(what)
}

export default function () {
    const res = http.post(MCP, JSON.stringify({
        jsonrpc: '2.0', id: 1, method: 'tools/call',
        params: { name: 'ask', arguments: { question: QUESTION }, _meta: META },
    }), {
        headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json, text/event-stream',
            'MCP-Protocol-Version': '2026-07-28',
            'mcp-method': 'tools/call',
            'mcp-name': 'ask',
            'X-Synthetic-Check': 'A2',
        },
        timeout: '60s',
    })
    must(res.status === 200, `MCP ask answers 200 (got ${res.status})`)

    let result = {}
    try {
        const rpc = res.json()
        must(!rpc.error && rpc.result && !rpc.result.isError, `tool call succeeds (${JSON.stringify(rpc.error || {})})`)
        result = rpc.result.structuredContent || JSON.parse(rpc.result.content[0].text)
    } catch (e) {
        fail(`answer is readable JSON (${e})`)
    }
    const answer = String(result.answer || '')
    const summary = (result.verification || {}).summary || {}
    must(!answer.includes('trouble responding'), 'not the apology (every model attempt failed)')
    must(/\b3E\b/.test(answer), `answer names 3E: "${answer.slice(0, 160)}"`)
    must(summary.checked >= 1, `at least one sentence checked (stamps: ${JSON.stringify(summary)})`)
    must(res.timings.duration < 15000, `answered within 15 s (${Math.round(res.timings.duration)} ms)`)
}
