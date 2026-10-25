/**
 * On-device tier: answer from the visitor's own machine with Chrome's built-in
 * model (the Prompt API, stable on desktop since Chrome 148), grounded in the same
 * profile the cloud agent uses. The question never leaves the computer.
 *
 * Escalation to the cloud is decided by verification, not by the model's own
 * confidence: every quote the model cites must appear verbatim in the profile
 * text it was given, and the model must say the profile supports the answer.
 * Anything else falls back to the cloud agent.
 */

const PROFILE_URL = '/profile.json'
const CHAR_BUDGET = 14000 // ~3.5k tokens of profile text, inside Nano's 9,216-token window with room for the answer
const STOPWORDS = new Set(('a an the and or of to in on at for with by from is are was were be been he his him it its this that these those what which who whom whose how when where why does do did has have had can could would should will about tell me more any some'.split(' ')))

let profileCache = null

export function isSupported() {
    return typeof window !== 'undefined' && 'LanguageModel' in window
}

export async function availability() {
    if (!isSupported()) return 'unavailable'
    try { return await window.LanguageModel.availability() } catch { return 'unavailable' }
}

export async function loadProfile() {
    if (profileCache) return profileCache
    const r = await fetch(PROFILE_URL, { cache: 'force-cache' })
    if (!r.ok) throw new Error(`profile ${r.status}`)
    profileCache = await r.json()
    return profileCache
}

/** Flatten the profile into labelled text blocks the model can quote from */
export function profileBlocks(p) {
    const blocks = []
    const add = (label, obj, always = false) => blocks.push({ label, text: typeof obj === 'string' ? obj : JSON.stringify(obj, null, 1), always })
    add('basics', { name: p.basics?.name, headline: p.basics?.headline, focus: p.basics?.focus, degree: p.basics?.degree_line,
        availability: p.basics?.availability, location: p.basics?.location, relocation: p.basics?.relocation, email: p.basics?.email,
        links: p.basics?.links }, true)
    add('faq', p.faq, true)
    add('about', p.about, true)
    add('looking_for', p.looking_for)
    ;(p.experience || []).forEach((e, i) => add(`experience[${i}] ${e.company}`, e))
    ;(p.projects || []).forEach((x, i) => add(`projects[${i}] ${x.title}`, x))
    ;(p.publications || []).forEach((x, i) => add(`publications[${i}]`, x))
    add('honors', p.honors); add('skills', p.skills); add('education', p.education); add('principles', p.principles)
    return blocks
}

function tokens(text) {
    return (text.toLowerCase().match(/[a-z0-9][a-z0-9.+#-]*/g) || []).filter(t => t.length > 2 && !STOPWORDS.has(t))
}

/** Pick the profile blocks most related to the question, within the character budget */
export function selectBlocks(blocks, question) {
    const q = new Set(tokens(question))
    const scored = blocks.map(b => {
        const words = tokens(b.label + ' ' + b.text)
        let hits = 0
        for (const w of words) if (q.has(w)) hits += 1
        return { ...b, score: b.always ? Infinity : hits / Math.sqrt(words.length + 1) }
    }).sort((a, b) => b.score - a.score)
    const chosen = []
    let used = 0
    for (const b of scored) {
        if (!b.always && used + b.text.length > CHAR_BUDGET) continue
        chosen.push(b); used += b.text.length
    }
    return chosen
}

function norm(s) {
    return (s || '').replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/\s+/g, ' ').trim().toLowerCase()
}

const SCHEMA = {
    type: 'object',
    properties: {
        answer: { type: 'string', description: 'Two to five plain sentences, third person, from the profile only' },
        quotes: { type: 'array', items: { type: 'string' }, description: 'Exact substrings of the PROFILE that support the answer' },
        supported: { type: 'boolean', description: 'true only if the profile contains the information asked for' },
    },
    required: ['answer', 'quotes', 'supported'],
}

const SYSTEM = `You answer questions about Rayhan Patel for recruiters, in the third person, using ONLY the PROFILE text you are given.
Rules: never invent facts, numbers or dates; copy every number exactly; if the profile does not contain the answer, set supported=false and say so in one sentence; keep answers to two to five plain sentences; no hype words.
Work authorization: say he is authorized to work in the US through OPT from June 2027 and that sponsorship questions go to rayhanbp@umd.edu.`

/**
 * Create a model session. `onProgress(fraction)` reports the one-time download.
 * Must be called from a user gesture the first time (the download needs one).
 */
export async function createSession(onProgress) {
    const session = await window.LanguageModel.create({
        initialPrompts: [{ role: 'system', content: SYSTEM }],
        monitor(m) {
            m.addEventListener('downloadprogress', e => onProgress && onProgress(e.loaded))
        },
    })
    return session
}

/**
 * Ask on-device. Resolves {ok:true, answer, quotes:[{text, verified}], timing} or {ok:false, reason}.
 * Falls back (ok:false) whenever the model is not supported by the profile.
 */
export async function answerOnDevice(session, question) {
    const profile = await loadProfile()
    const blocks = selectBlocks(profileBlocks(profile), question)
    const context = blocks.map(b => `[${b.label}]\n${b.text}`).join('\n\n')
    const contextNorm = norm(context)
    const prompt = `PROFILE:\n${context}\n\nQUESTION: ${question}`

    const t0 = performance.now()
    const raw = await session.prompt(prompt, { responseConstraint: SCHEMA })
    const elapsed = Math.round(performance.now() - t0)

    let parsed
    try { parsed = JSON.parse(raw) } catch { return { ok: false, reason: 'unparseable', timing: elapsed } }
    const answer = (parsed.answer || '').trim()
    const quotes = (parsed.quotes || []).map(q => ({ text: q, verified: norm(q).length >= 8 && contextNorm.includes(norm(q)) }))
    const verified = quotes.filter(q => q.verified)

    if (!answer) return { ok: false, reason: 'empty', timing: elapsed }
    if (parsed.supported === false) return { ok: false, reason: 'not_in_profile', answer, timing: elapsed }
    if (verified.length === 0) return { ok: false, reason: 'no_verified_quote', answer, timing: elapsed }

    // Numbers must come from the profile text the model saw
    const nums = (answer.match(/\d[\d,]*(?:\.\d+)?%?/g) || [])
    const badNums = nums.filter(n => !contextNorm.replace(/,/g, '').includes(n.replace(/,/g, '').toLowerCase()))
    if (badNums.length) return { ok: false, reason: 'unverified_number', answer, badNums, timing: elapsed }

    return { ok: true, answer, quotes, timing: elapsed, blocks: blocks.length, chars: context.length }
}
