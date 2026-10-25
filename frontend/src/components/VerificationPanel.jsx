import { useState, memo } from 'react'
import { ShieldCheck, ShieldAlert, ShieldQuestion, ChevronDown, ChevronUp } from 'lucide-react'

/**
 * VerificationPanel
 *
 * Shows the stamps the backend puts on each sentence of an answer:
 *   checked   quote from the profile, verified in code
 *   inferred  the agent's own summary or reasoning
 *   unknown   not in the profile (the agent should have said so)
 * Sentences that aren't claims (greetings, contact pointers) are left out.
 */
const LABELS = {
    checked: { text: 'checked', color: 'text-emerald-300', dot: 'bg-emerald-400', Icon: ShieldCheck },
    inferred: { text: 'inferred', color: 'text-amber-300', dot: 'bg-amber-400', Icon: ShieldQuestion },
    unknown: { text: 'not in profile', color: 'text-red-300', dot: 'bg-red-400', Icon: ShieldAlert },
    unverified: { text: 'unverified', color: 'text-gray-400', dot: 'bg-gray-500', Icon: ShieldQuestion },
}

function VerificationPanel({ verification, verifying }) {
    const [open, setOpen] = useState(false)

    if (verifying) {
        return (
            <div className="mt-3 pt-2 border-t border-white/10 flex items-center gap-2 text-xs text-gray-400">
                <span className="w-2 h-2 rounded-full bg-primary-400 animate-pulse" aria-hidden="true"></span>
                Checking this answer against the profile…
            </div>
        )
    }
    if (!verification) return null

    const claims = (verification.claims || []).filter(c => c.status !== 'n/a')
    if (claims.length === 0) return null
    const s = verification.summary || {}
    const parts = []
    if (s.checked) parts.push(`${s.checked} checked`)
    if (s.inferred) parts.push(`${s.inferred} inferred`)
    if (s.unknown) parts.push(`${s.unknown} not in profile`)
    if (s.unverified) parts.push(`${s.unverified} unverified`)
    if (s.numbers_unverified) parts.push(`${s.numbers_unverified} with unverified numbers`)
    const allGood = claims.every(c => c.status === 'checked') && !s.numbers_unverified
    const HeadIcon = allGood ? ShieldCheck : ShieldQuestion

    return (
        <div className="mt-3 pt-2 border-t border-white/10 text-xs">
            <button
                type="button"
                onClick={() => setOpen(v => !v)}
                className="flex items-center gap-2 text-gray-300 hover:text-white transition-colors w-full text-left"
                aria-expanded={open}
            >
                <HeadIcon className={`w-4 h-4 ${allGood ? 'text-emerald-300' : 'text-amber-300'}`} />
                <span className="flex-1">
                    Verified against profile {verification.as_of ? `(${verification.as_of})` : ''}: {parts.join(' · ')}
                </span>
                {open ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>

            {open && (
                <ul className="mt-2 space-y-2" aria-label="Claims and evidence">
                    {claims.map((c, i) => {
                        const meta = LABELS[c.status] || LABELS.unverified
                        return (
                            <li key={i} className="flex gap-2">
                                <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${meta.dot}`} aria-hidden="true"></span>
                                <div className="min-w-0">
                                    <div className="text-gray-200">
                                        {c.text}{' '}
                                        <span className={`${meta.color} font-medium`}>· {meta.text}</span>
                                        {c.numbers_unverified && c.numbers_unverified.length > 0 && (
                                            <span className="text-amber-300"> · numbers not in profile: {c.numbers_unverified.join(', ')}</span>
                                        )}
                                    </div>
                                    {c.evidence && c.evidence.quote && (
                                        <div className="text-gray-400 mt-0.5">
                                            <span className="text-gray-500">{c.evidence.verified ? 'profile says' : 'closest text'}:</span>{' '}
                                            “{c.evidence.quote}”
                                            {c.evidence.path && <span className="text-gray-600"> ({c.evidence.path})</span>}
                                        </div>
                                    )}
                                </div>
                            </li>
                        )
                    })}
                </ul>
            )}
        </div>
    )
}

export default memo(VerificationPanel)
