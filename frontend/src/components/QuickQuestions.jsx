import { memo } from 'react'
import { Briefcase, Code, Lightbulb, Sparkles } from 'lucide-react'

/**
 * QuickQuestions Component
 * 
 * Displays 4 quick question buttons that users can click to ask
 * pre-defined questions about Rayhan's background.
 * Supports dynamic prompts from backend or company-specific overrides.
 */
function QuickQuestions({ onSelectQuestion, disabled, company, customPrompts }) {
    // Base questions (default)
    const baseQuestions = [
        {
            id: 1,
            icon: Sparkles,
            text: "Why should we hire Rayhan?",
            color: "from-purple-500 to-indigo-600",
            hoverColor: "hover:shadow-purple-500/30"
        },
        {
            id: 2,
            icon: Code,
            text: "What's his ML experience?",
            color: "from-blue-500 to-cyan-600",
            hoverColor: "hover:shadow-blue-500/30"
        },
        {
            id: 3,
            icon: Lightbulb,
            text: "Tell me about his projects",
            color: "from-amber-500 to-orange-600",
            hoverColor: "hover:shadow-amber-500/30"
        },
        {
            id: 4,
            icon: Briefcase,
            text: "What makes him unique?",
            color: "from-emerald-500 to-teal-600",
            hoverColor: "hover:shadow-emerald-500/30"
        }
    ]

    // Company-specific question overrides (fallback if no customPrompts)
    const companyQuestions = {
        google: [
            "Why would Rayhan fit Google's AI team?",
            "Experience with large-scale ML systems?"
        ],
        meta: [
            "How does Rayhan align with Meta AI?",
            "Recommendation systems experience?"
        ],
        amazon: [
            "Why would Rayhan excel at Amazon?",
            "His cloud/AWS experience?"
        ],
        apple: [
            "Rayhan's fit for Apple ML?",
            "Privacy-focused AI experience?"
        ],
        microsoft: [
            "Rayhan for Microsoft Research?",
            "His published research work?"
        ],
        nvidia: [
            "Rayhan for NVIDIA's AI team?",
            "GPU optimization experience?"
        ],
        openai: [
            "Why Rayhan for OpenAI?",
            "His LLM/RAG experience?"
        ],
        anthropic: [
            "Rayhan's fit for Anthropic?",
            "AI safety research experience?"
        ]
    }

    // Priority: customPrompts (from backend) > company-specific > base
    let questions = baseQuestions

    if (customPrompts && customPrompts.length === 4) {
        // Use LLM-generated prompts from backend
        questions = baseQuestions.map((q, i) => ({
            ...q,
            text: customPrompts[i]
        }))
    } else {
        // Fall back to company-specific overrides
        const normalized = company?.toLowerCase().trim()
        const overrides = companyQuestions[normalized]
        if (overrides) {
            questions = baseQuestions.map((q, i) =>
                i < overrides.length ? { ...q, text: overrides[i] } : q
            )
        }
    }

    return (
        <div className="grid grid-cols-2 gap-2 sm:gap-3 mb-4 landscape-qs-row">
            {questions.map((question) => {
                const IconComponent = question.icon
                return (
                    <button
                        key={question.id}
                        onClick={() => onSelectQuestion(question.text)}
                        disabled={disabled}
                        className={`quick-btn flex flex-col sm:flex-row items-center sm:items-center justify-center sm:justify-start gap-2 sm:gap-3 px-3 py-3 sm:px-4 sm:py-3.5 rounded-xl
                       bg-white/5 backdrop-blur-sm
                       border border-white/10 hover:border-white/20
                       text-white text-xs sm:text-sm font-medium text-center sm:text-left
                       shadow-lg ${question.hoverColor}
                       disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100
                       group h-full`}
                    >
                        <div className={`p-1.5 sm:p-2 rounded-lg bg-gradient-to-br ${question.color} shadow-md group-hover:scale-110 transition-transform duration-300 shrink-0`}>
                            <IconComponent className="w-3 h-3 sm:w-4 sm:h-4 text-white" />
                        </div>
                        <span className="group-hover:text-white/90 line-clamp-2">{question.text}</span>
                    </button>
                )
            })}
        </div>
    )
}

export default memo(QuickQuestions)
