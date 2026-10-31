# Architecture

How the agent behind [chat.rayhanpatel.com](https://chat.rayhanpatel.com) and [rayhanpatel.com](https://rayhanpatel.com) is built, from the outside in. The structural views follow the [C4 model](https://c4model.com) (context, containers, components); the behavioural views are UML sequence, state and entity-relationship diagrams. Every diagram has a title, a key, a type and technology on each box, and a label on each arrow. Last checked against the code on Oct 31, 2026 (backend 2.11.0).

**Contents:** [Goals](#goals-and-constraints) · [1 Context](#1-system-context) · [2 Containers](#2-containers) · [3 Backend components](#3-backend-components) · [4 Starting a chat](#4-starting-a-chat) · [5 Answering a question](#5-answering-a-question) · [6 Model fallback](#6-model-fallback-state-machine) · [7 Stamping a sentence](#7-how-a-sentence-gets-its-stamp) · [8 Agents over MCP](#8-agents-over-mcp-and-the-on-device-tier) · [9 Data](#9-data-model-and-where-data-goes) · [10 Deployment](#10-deployment-and-delivery) · [11 Monitoring](#11-monitoring-and-alerting) · [12 Threats](#12-threats-and-defences) · [13 Decisions](#13-decisions)

## Goals and constraints

| Quality goal | What it means here | Where it shows up |
|---|---|---|
| **Truthful** | Say only what the public profile backs, and show which sentences are backed | One profile file ([§9](#9-data-model-and-where-data-goes)), verification stamps ([§7](#7-how-a-sentence-gets-its-stamp)), the golden eval gate ([§10](#10-deployment-and-delivery)) |
| **Bounded cost** | A stranger can't run up the bill | Quotas, a daily token budget, a $25/month Google Cloud spend cap ([§5](#5-answering-a-question)) |
| **Hard to turn against him** | An attacker can't make it leak, lie about him, or say something damaging | No secrets or tools in the model's context, rules for each attack type, stamps on every sentence ([§12](#12-threats-and-defences)) |
| **Observable by one person** | A broken chat reaches the owner's phone in minutes, with a runbook | Checks from outside, OpenTelemetry, alert routing ([§11](#11-monitoring-and-alerting)) |

**Constraints:** one developer; free tiers (Supabase pauses after 7 quiet days, Grafana keeps 14 days); a few visitors a day, so scheduled probes, not traffic, are the steady signal; Google retires model IDs on a schedule.

---

## 1. System context

*C4 level 1. Who uses the system and what it depends on.*

```mermaid
flowchart TB
    accTitle: System context of Rayhan's agent
    accDescr: Visitors and AI agents use the system. It calls Google Vertex AI, OpenRouter, Supabase, Langfuse and Grafana Cloud. Grafana probes it and pages Rayhan, and GitHub deploys it after tests and the eval pass.
    visitor["<b>Visitor</b><br/>[Person]<br/>Recruiter or engineer"]
    agentclient["<b>AI agent</b><br/>[External client]<br/>Another assistant, over MCP"]
    owner["<b>Rayhan</b><br/>[Person]<br/>Owner, on call"]

    system["<b>Rayhan's agent</b><br/>[Software system]<br/>Site, chat and API: answers questions<br/>about his work and stamps every sentence"]

    vertex["<b>Google Vertex AI</b><br/>[External]<br/>Gemini 3.5 Flash and Flash-Lite"]
    openrouter["<b>OpenRouter</b><br/>[External]<br/>Decision model (Jev): off-topic gate,<br/>second opinion on stamps; judge model"]
    supabase["<b>Supabase</b><br/>[External]<br/>Postgres: sessions and events"]
    langfuse["<b>Langfuse</b><br/>[External]<br/>Traces with text, five LLM judges"]
    grafana["<b>Grafana Cloud</b><br/>[External]<br/>Telemetry, checks, alerts, IRM"]
    github["<b>GitHub</b><br/>[External]<br/>Code, CI, eval gate, schedules"]

    visitor -->|"reads the site, chats<br/>[HTTPS, SSE]"| system
    agentclient -->|"asks questions<br/>[MCP over HTTPS]"| system
    system -->|"answers, checks<br/>[HTTPS]"| vertex
    system -->|"off-topic gate, second opinion<br/>[HTTPS]"| openrouter
    system -->|"reads and writes sessions, events<br/>[HTTPS]"| supabase
    system -->|"sends traces<br/>[HTTPS, SDK]"| langfuse
    system -->|"sends telemetry<br/>[OTLP]"| grafana
    grafana -->|"probes from outside<br/>[HTTPS, browser]"| system
    grafana -->|"pages and emails<br/>[mobile push, email]"| owner
    owner -->|"pushes changes<br/>[git]"| github
    github -->|"deploys after tests and eval pass<br/>[webhook]"| system

    subgraph key["Key"]
        k1["Person"]:::person
        k2["This system"]:::sys
        k3["External system"]:::ext
    end
    style key fill:none,stroke:#9ca3af,color:#6b7280

    classDef person fill:#0b3d6e,stroke:#0b3d6e,color:#ffffff
    classDef sys fill:#1d6fb8,stroke:#0b3d6e,color:#ffffff
    classDef ext fill:#e5e7eb,stroke:#6b7280,color:#111827
    class visitor,agentclient,owner person
    class system sys
    class vertex,openrouter,supabase,langfuse,grafana,github ext
```

Also used, left out above for size: Cloudflare (DNS, Turnstile bot check, Workers for the site), Vercel (chat page hosting), Railway (API hosting), a Google Sheet (recruiter leads).

---

## 2. Containers

*C4 level 2. The separately running pieces inside the system, and how they talk.*

```mermaid
flowchart TB
    accTitle: Containers of Rayhan's agent
    accDescr: The site, the chat page, the on-device tier, the API and the database, with profile.json feeding the site, the API and the on-device tier, and the external Turnstile, model and observability services.
    visitor["<b>Visitor</b><br/>[Person]"]
    agentclient["<b>AI agent</b><br/>[External client]"]

    subgraph sys["Rayhan's agent [Software system]"]
        site["<b>Site</b><br/>[Container: static HTML]<br/>rayhanpatel.com on Cloudflare Workers"]
        spa["<b>Chat page</b><br/>[Container: React 18, Vite]<br/>chat.rayhanpatel.com on Vercel"]
        device["<b>On-device tier</b><br/>[Container: Chrome Prompt API]<br/>Gemini Nano in the visitor's browser"]
        api["<b>API</b><br/>[Container: FastAPI, Python 3.11]<br/>Railway: sessions, chat, MCP, health"]
        db[("<b>Database</b><br/>[Container: Postgres on Supabase]<br/>sessions, events")]
        profile[/"<b>profile.json</b><br/>[Data file]<br/>The only source of facts"/]
    end

    turnstile["<b>Turnstile</b><br/>[External: Cloudflare]"]
    models["<b>Vertex AI + OpenRouter</b><br/>[External: model APIs]"]
    obs["<b>Langfuse + Grafana Cloud</b><br/>[External: observability]"]

    visitor -->|"loads [HTTPS]"| site
    site -->|"embeds [iframe]"| spa
    visitor -->|"chats [HTTPS]"| spa
    spa -->|"session, chat [JSON, SSE]"| api
    spa -->|"bot check token [JS widget]"| turnstile
    spa -->|"asks on-device first,<br/>when Chrome supports it [JS]"| device
    agentclient -->|"ask, fit_report [MCP over HTTPS]"| api
    api -->|"verifies token [HTTPS]"| turnstile
    api -->|"prompts [HTTPS]"| models
    api -->|"reads and writes [PostgREST]"| db
    api -->|"traces, metrics, logs [SDK, OTLP]"| obs
    spa -->|"browser errors [Faro]"| obs
    profile -.->|"rendered at build"| site
    profile -.->|"copied in at deploy"| api
    profile -.->|"fetched"| device

    subgraph key["Key"]
        k1["Container in this system"]:::cont
        k3["External system"]:::ext
        k4[/"Data file"/]:::data
        k5["solid arrow: runtime call<br/>dashed arrow: build or deploy time"]:::note
    end
    style key fill:none,stroke:#9ca3af,color:#6b7280
    style sys fill:none,stroke:#1d4f80,color:#6b7280,stroke-dasharray: 5 3

    classDef person fill:#0b3d6e,stroke:#0b3d6e,color:#ffffff
    classDef cont fill:#3b82c4,stroke:#1d4f80,color:#ffffff
    classDef data fill:#fef3c7,stroke:#b45309,color:#111827
    classDef ext fill:#e5e7eb,stroke:#6b7280,color:#111827
    classDef note fill:none,stroke:none,color:#6b7280
    class visitor,agentclient person
    class site,spa,device,api,db cont
    class profile data
    class turnstile,models,obs ext
```

The API is called directly, not through Cloudflare's proxy, so long streamed answers aren't cut by proxy timeouts. That puts rate limiting, quotas and input sanitisation in the API itself ([§3](#3-backend-components)).

---

## 3. Backend components

*C4 level 3. Inside the API container. Thin routes; services do the work; every external dependency has an `enabled` flag so the chat degrades instead of failing.*

```mermaid
flowchart TB
    accTitle: Backend components in five layers
    accDescr: A request passes middleware, a thin route, guards that can refuse it, services that do the work, and adapters to external services. A background loop maintains the database.
    req["<b>HTTP request</b><br/>chat page, MCP client or probe"]

    subgraph L1["1 · Middleware: every request, outermost first"]
        direction LR
        m1["OpenTelemetry<br/>span + metrics"] --> m2["Origin secret<br/>(off until proxied)"] --> m3["Request ID"] --> m4["CORS"] --> m5["Probe tag<br/>X-Synthetic-Check"]
    end
    subgraph L2["2 · Routes: thin"]
        direction LR
        r1["/session"] ~~~ r2["/chat<br/>/chat/stream"] ~~~ r3["/mcp"] ~~~ r4["/health<br/>/health/llm"] ~~~ r5["/admin"]
    end
    subgraph L3["3 · Guards: refuse with 403 or 429"]
        direction LR
        g1["SessionTokens<br/>HMAC, 24 h"] ~~~ g2["RateLimiter<br/>per IP, per minute"] ~~~ g3["QuotaService<br/>session, IP, daily tokens"] ~~~ g4["MCPLimits<br/>+ MCP token budget"]
    end
    subgraph L4["4 · Services: do the work"]
        direction LR
        s1["JevIntentGate<br/>previous turn, fails open"] ~~~ s2["AgentService<br/>model fallback"] ~~~ s3["Verifier +<br/>DecisionsGate"] ~~~ s4["JobExtractor +<br/>JobParser"] ~~~ s5["Leads"]
    end
    subgraph L5["5 · Adapters: each has an enabled flag, so the chat degrades"]
        direction LR
        a1["Gemini client<br/>raw async HTTP/2"] ~~~ a2["SupabaseService"] ~~~ a3["TracingService<br/>Langfuse"] ~~~ a4["Telemetry<br/>OTLP"] ~~~ a5["Turnstile,<br/>Sheet webhook"]
    end
    bg["<b>maintain_data</b> [background loop]<br/>DB ping every 6 h · 90-day deletion daily · row counts"]

    req --> L1 --> L2 --> L3 --> L4 --> L5
    bg --> L5

    classDef layer fill:#3b82c4,stroke:#1d4f80,color:#ffffff
    classDef guard fill:#b45309,stroke:#78350f,color:#ffffff
    classDef plain fill:#ffffff,stroke:#6b7280,color:#111827
    class m1,m2,m3,m4,m5,r1,r2,r3,r4,r5,s1,s2,s3,s4,s5,a1,a2,a3,a4,a5,bg layer
    class g1,g2,g3,g4 guard
    class req plain
    style L1 fill:none,stroke:#9ca3af,color:#6b7280
    style L2 fill:none,stroke:#9ca3af,color:#6b7280
    style L3 fill:none,stroke:#b45309,color:#b45309
    style L4 fill:none,stroke:#9ca3af,color:#6b7280
    style L5 fill:none,stroke:#9ca3af,color:#6b7280
```

Which route uses which components:

| Route | Guards | Services and adapters |
|---|---|---|
| `POST /session` | RateLimiter, QuotaService (sessions per IP) | Turnstile, JobExtractor + JobParser, SupabaseService, Leads, SessionTokens (issues the token) |
| `POST /chat/stream`, `/chat` | SessionTokens, RateLimiter, QuotaService | JevIntentGate (sees the previous turn; lets the message through if it fails), AgentService, Verifier + DecisionsGate, SupabaseService, TracingService |
| `POST /mcp` | MCPLimits | AgentService, Verifier + DecisionsGate, TracingService (tag `mcp`) |
| `GET /health/llm` | none | Gemini client (cached 10 min), SupabaseService ping |
| `GET /admin/*` | `X-Admin-Key` | SupabaseService |

---

## 4. Starting a chat

*UML sequence. `POST /api/v1/session`: the only call that needs the bot check, and the one that issues the signed token every later call must carry.*

```mermaid
sequenceDiagram
    accTitle: Sequence: starting a chat
    accDescr: The chat page gets a Turnstile token, the API verifies it, optionally reduces a job post to a schema, creates the session, records a lead and returns a signed token.
    autonumber
    actor V as Visitor
    participant F as Chat page
    participant T as Turnstile
    participant A as API /session
    participant G as Gemini Flash-Lite
    participant D as Supabase
    participant S as Google Sheet

    V->>F: name, company, optional job link or text
    F->>T: run the bot check
    T-->>F: token
    F->>A: POST /session with the token
    A->>A: rate limit, sessions per IP per day
    A->>T: siteverify(token)
    T-->>A: success (else 403)
    opt a job post was given
        A->>A: fetch the URL (SSRF-checked, no redirects)
        A->>G: extract a fixed schema (title, level, skills, summary)
        G-->>A: schema JSON. The raw page is discarded.
    end
    A->>D: create session (name, company, job schema, hashed IP)
    opt a job post was read and this is not a probe
        A-)S: lead (name, company, role), reply checked
    end
    A-->>F: session ID + signed token + welcome with AI disclosure
```

---

## 5. Answering a question

*UML sequence. `POST /api/v1/chat/stream`. Words stream first; the stamps follow the same stream, so the visitor never waits for verification.*

```mermaid
sequenceDiagram
    accTitle: Sequence: answering a question
    accDescr: The API checks the token, loads the session, applies limits, asks the decision model whether the question is on topic, streams the answer, then verifies each sentence and streams the stamps before saving the turn and closing the trace.
    autonumber
    actor V as Visitor
    participant F as Chat page
    participant A as API /chat/stream
    participant D as Supabase
    participant G as Gemini (Vertex AI)
    participant J as Jev (OpenRouter)
    participant L as Langfuse

    V->>F: question
    F->>A: POST with session ID + signed token
    A->>A: verify token (403 if forged or expired)
    A->>D: load session and history (3 s timeout, memory fallback)
    A->>A: rate limit, quotas: session, IP, daily tokens (429 if over)
    A->>L: open trace
    A->>J: on topic? P(yes), with the profile's names and the previous turn (1 s, on error or timeout it is allowed)
    alt P below 0.75 (first message) or 0.5 (after a turn)
        A-->>F: fixed polite decline (kept in the history), done
    else about Rayhan
        A->>G: stream answer, Flash (fallback in §6)
        loop each chunk
            G-->>A: text
            A-->>F: data: chunk
            F-->>V: words appear
        end
        A-->>F: data: status verifying
        A->>G: verdict + verbatim quote per sentence, Flash-Lite JSON
        A->>A: code checks each quote is in the profile and every number
        A->>J: probability each sentence holds (can only demote)
        A-->>F: data: verification (stamp per sentence)
        A-->>F: data: DONE
        par after the response
            A-)D: save the turn, log the event and tokens
            A-)L: close the trace (judges score it later)
        end
    end
```

---

## 6. Model fallback (state machine)

*UML state diagram. `AgentService` tries models in order; a model that errors, returns nothing, or sends no first token in time hands over to the next. Every switch is counted (`chat.model_fallbacks`).*

```mermaid
stateDiagram-v2
    accTitle: State machine: model fallback
    accDescr: Gemini 3.5 Flash is tried first, then Flash-Lite, then Flash-Lite with a longer wait, and an apology is sent if all fail.
    [*] --> Flash
    state "Gemini 3.5 Flash<br/>first token within 10 s" as Flash
    state "Gemini 3.5 Flash-Lite<br/>first token within 15 s" as Lite
    state "Flash-Lite, last try<br/>first token within 45 s" as LiteLong
    state "Apology sent<br/>counted as outcome=apology" as Apology
    Flash --> Answered: tokens arrive
    Flash --> Lite: error, empty or silent
    Lite --> Answered: tokens arrive
    Lite --> LiteLong: error, empty or silent
    LiteLong --> Answered: tokens arrive
    LiteLong --> Apology: error, empty or silent
    Answered --> [*]
    Apology --> [*]
```

Model IDs are settings (`GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL`), because Google retires them: a retired ID returns 404, and in 2026 that went unnoticed for four months ([post-mortem](POSTMORTEM-2026-10-chatbot-outage.md)). A weekly workflow now fails 60 days before a configured model's retirement date.

---

## 7. How a sentence gets its stamp

*Decision flow inside `services/verifier.py`. The model only proposes; code decides.*

```mermaid
flowchart TB
    accTitle: Decision flow: how a sentence gets its stamp
    accDescr: The model's verdict and quote are checked by code against the profile, the decision model can demote checked to inferred, a sentence that speaks for the profile without a real quote becomes unknown unless the decision model backs it, and every number is checked.
    s["Sentence from the answer"] --> v{"Flash-Lite verdict<br/>(with a verbatim quote;<br/>one retry if the call fails)"}
    v -->|"no verdict returned"| unv["<b>unverified</b>"]
    v -->|"not a claim<br/>(greeting, offer, refusal,<br/>contact pointer, 'no information')"| na["<b>n/a</b>"]
    v -->|"not in profile"| unk["<b>unknown</b>"]
    v -->|"inferred"| att{"Says 'the profile states…'<br/>with no real quote, and Jev<br/>below 0.5 or off?"}
    att -->|"yes"| unk
    att -->|"no"| inf["<b>inferred</b>"]
    v -->|"supported"| q{"Does code find the quote<br/>verbatim in profile.json?"}
    q -->|"no"| att
    q -->|"yes"| jev{"Jev probability<br/>below 0.5?"}
    jev -->|"yes: demote"| inf
    jev -->|"no, or Jev off"| chk["<b>checked</b>"]
    chk --> num{"Every number in the sentence<br/>appears in the profile?"}
    inf --> num
    num -->|"no"| flag["flag: numbers_unverified"]
    num -->|"yes"| done["stamp sent to the page"]
    flag --> done

    subgraph key["Key"]
        k1{"Decision"}
        k2["<b>Stamp</b>"]:::stamp
    end
    style key fill:none,stroke:#9ca3af,color:#6b7280

    classDef stamp fill:#3b82c4,stroke:#1d4f80,color:#ffffff
    class unv,na,unk,inf,chk stamp
```

On the golden set (20 questions against the live model, four of them attacks), 96% of 51 claims were `checked` on Oct 31, 2026, with 0 unverified numbers ([eval report](EVAL-REPORT.md)). The "speaks for the profile" rule exists because, asked about a made-up cheating rumour, the bot once said "the profile states that Rayhan is in good academic standing", and the stamp was `inferred`.

---

## 8. Agents over MCP and the on-device tier

*Two other ways in. Both reuse the same agent and the same stamps.*

```mermaid
sequenceDiagram
    accTitle: Sequence: MCP clients and the on-device tier
    accDescr: An MCP client's question passes the MCP limits and gets the same stamped answer. In Chrome, the on-device model answers first and falls back to the cloud if a quote or number fails.
    autonumber
    participant C as AI agent (MCP client)
    participant M as API /mcp
    participant AG as AgentService + Verifier
    actor V as Visitor (Chrome with Gemini Nano)
    participant N as On-device model
    participant A as API /chat/stream

    Note over C,AG: MCP 2026-07-28, stateless: every request carries its own metadata
    C->>M: tools/call ask(question)
    M->>M: rate limit, per-caller limit, MCP daily token budget (429 if over)
    M->>AG: answer + stamps (traced in Langfuse as mcp)
    AG-->>C: answer, model, verification

    Note over V,A: Chrome Prompt API: the question stays on the computer
    V->>N: question + the matching profile blocks
    N-->>V: answer + the quotes it relied on
    alt every quote is verbatim in the profile and every number checks
        V->>V: show the on-device answer, report timing only
    else a quote or number fails
        V->>A: fall back to the cloud agent, visibly
    end
```

---

## 9. Data model and where data goes

*UML entity-relationship diagram of the two Postgres tables, then every place visitor data can reach.*

```mermaid
erDiagram
    accTitle: Entity-relationship diagram of the database
    accDescr: A session has zero or more events; MCP events have no session. Sessions hold the visitor's name, company, history and job schema.
    SESSIONS |o--o{ EVENTS : "has 0..n (cascade delete); MCP events have none"
    SESSIONS {
        uuid id PK
        string user_name "typed by the visitor"
        string company
        jsonb preferences "history (last 10 turns), job schema, ip_hash"
        timestamptz created_at
        timestamptz last_active "deleted 90 days after this"
    }
    EVENTS {
        bigint id PK "BIGSERIAL"
        uuid session_id FK "null for MCP calls"
        string event_type "session_start, chat, chat_stream, chat_device, mcp, mcp_tokens"
        string intent
        int tokens_in
        int tokens_out
        int latency_ms
        jsonb metadata "ip_hash (salted), caller for MCP"
        timestamptz created_at
    }
```

| Data | Stored in | Kept | Never contains |
|---|---|---|---|
| Name, company, job schema, chat history | Supabase `sessions` | 90 days after last activity (daily purge) | the raw job page, the IP address |
| Questions and answers | Langfuse traces (judged by an LLM through OpenRouter); each question, with the previous question and the start of the previous reply, also goes to the decision model for the off-topic gate (zero data retention requested) | 90 days (the app purges; the free plan doesn't) | the name field or IP address (user ID is a salted IP hash) |
| Lead: name, company, role, link | Google Sheet | his contact record | chat text |
| Model names, tokens, timings, outcomes | Grafana Cloud (OTLP) | 14 days | message text, names |
| Browser errors, page speed, continent | Grafana Faro | 14 days | URL query strings (cut at `?`), names |
| Page views, Web Vitals | Vercel Analytics | Vercel's terms | cookies, URL query strings |

---

## 10. Deployment and delivery

*Where each container runs, and what has to pass before a change reaches it.*

```mermaid
flowchart TB
    accTitle: Deployment and delivery
    accDescr: A push to main runs the unit tests and the golden eval, which gate the Railway deploy. Vercel and Cloudflare Workers build on push. Scheduled workflows probe the API.
    dev["<b>git push to main</b><br/>[Rayhan]"]

    subgraph gh["GitHub Actions"]
        ci["<b>ci.yml</b><br/>unit tests incl. docs-match-code, frontend build"]
        eval["<b>eval.yml</b><br/>16-question golden eval vs live model<br/>(keyless Google sign-in)"]
        sched["<b>Schedules</b><br/>A2 real answer every 6 h, uptime daily,<br/>model retirement weekly"]
    end

    subgraph run["Runtime"]
        railway["<b>Railway</b><br/>API container, 1 replica<br/>waits for CI ('Wait for CI')"]
        vercel["<b>Vercel</b><br/>chat page"]
        workers["<b>Cloudflare Workers</b><br/>rayhanpatel.com (Portfolio-V2 repo)"]
    end

    dev --> ci & eval
    ci -->|"green"| railway
    eval -->|"green"| railway
    dev -->|"build on push"| vercel
    dev -->|"build on push (site repo)"| workers
    sched -->|"probe, report to Grafana"| railway

    subgraph key["Key"]
        k1["Gate: a failure stops the deploy"]:::gate
        k2["Runtime host"]:::host
    end
    style key fill:none,stroke:#9ca3af,color:#6b7280

    classDef gate fill:#b45309,stroke:#78350f,color:#ffffff
    classDef host fill:#3b82c4,stroke:#1d4f80,color:#ffffff
    class ci,eval gate
    class railway,vercel,workers host
    style gh fill:none,stroke:#9ca3af,color:#6b7280
    style run fill:none,stroke:#9ca3af,color:#6b7280
```

The chat page (Vercel) and the site deploy without the eval gate; only the backend waits for it. Dependabot opens grouped dependency PRs, and those go through the same checks.

---

## 11. Monitoring and alerting

*Signals, where they go, and who gets woken up. Details and the rebuild steps: [`monitoring/README.md`](../monitoring/README.md); every alert links a [runbook](runbooks/).*

```mermaid
flowchart TB
    accTitle: Monitoring and alert routing
    accDescr: Synthetic checks, backend telemetry, browser data and CI results feed alert rules. Every alert is emailed; broken chat or site alerts also page the phone through Grafana IRM.
    subgraph sources["Signals"]
        otlp["<b>Backend telemetry</b><br/>[OTLP] traces, metrics, logs"]
        faro["<b>Browser</b><br/>[Faro] errors, page speed"]
        probes["<b>9 Synthetic checks</b><br/>[Grafana probes] chat page, health,<br/>site, DNS, domain, TLS"]
        ciresults["<b>CI results</b><br/>[GitHub Actions] A2 answer, golden eval"]
    end

    subgraph grafana["Grafana Cloud"]
        rules["<b>Alert rules</b><br/>17 rules + per-check alerts"]
        policy{"<b>Notification policy</b>"}
    end

    email["<b>Email</b><br/>everything"]
    irm["<b>Grafana IRM</b><br/>escalation chain"]
    phone["<b>Phone</b><br/>[mobile push]"]

    otlp & faro & probes & ciresults --> rules --> policy
    policy -->|"all alerts"| email
    policy -->|"chat or site broken,<br/>severity=page"| irm --> phone

    subgraph key["Key"]
        k1["Pages the owner"]:::page
        k2["Email only"]:::mail
    end
    style key fill:none,stroke:#9ca3af,color:#6b7280

    classDef page fill:#b91c1c,stroke:#7f1d1d,color:#ffffff
    classDef mail fill:#3b82c4,stroke:#1d4f80,color:#ffffff
    class irm,phone page
    class email mail
    style sources fill:none,stroke:#9ca3af,color:#6b7280
    style grafana fill:none,stroke:#9ca3af,color:#6b7280
```

| Pages the phone | Emails only |
|---|---|
| Chat page (B1) fails twice in 30 min; model or database health (A1) fails 3 times in 15 min from two locations; the site (S1) fails twice in 30 min; any rule labelled `severity=page` | A certificate within 14 days of expiry; fallback model on over 20% of answers; apology or error answers; unverifiable answer sentences above 20%; a lead not saved; the decision model failing; the 90-day deletion failing; database growth; tokens near the daily budget; slow first words; the A2 real answer failing or missing; a golden eval regression; DNS or domain problems |

Tested Oct 30, 2026: a rule labelled `severity=page` reached the phone 32 seconds after it fired. A2 emails rather than pages because GitHub delays and skips scheduled runs.

---

## 12. Threats and defences

*Defence in depth, in the order a message meets the layers. The structural layers (nothing to steal, nothing to act with, untrusted text reduced to a schema) carry the risk; the prompt rules and stamps shape what gets said.*

```mermaid
flowchart LR
    accTitle: Threats and defences, in the order a message meets them
    accDescr: An attacker's message passes the edge limits, the off-topic gate, the answer model with its rules and only the public profile in context, and the verifier's stamps before the reply is shown on the chat page. A pasted job post goes through a tool-less extraction call and reaches the answer model only as a fixed schema. Model calls are billed under a spend cap, and evals and alerts watch the answer layer.
    attacker["<b>Attacker</b><br/>[Person]"]
    jobpost[/"<b>Pasted job post</b><br/>[Untrusted text or URL]"/]
    seen["<b>What the attacker sees</b><br/>[Chat page]"]

    subgraph sys["Rayhan's agent [Software system]"]
        edge["<b>1 Edge limits</b><br/>[API + Turnstile]<br/>stops floods, forged sessions,<br/>runaway token use"]
        gate["<b>2 Off-topic gate</b><br/>[Jev decision model]<br/>stops unrelated requests<br/>and plain jailbreaks"]
        answer["<b>3 Answer model and rules</b><br/>[Gemini 3.5 Flash]<br/>sees the public profile only:<br/>no keys, no tools, no other visitors<br/>rules for rumours, false premises,<br/>fake overrides, prompt requests, roasts"]
        stamps["<b>4 Stamps</b><br/>[Verifier + code checks]<br/>marks sentences<br/>the profile doesn't back"]
        extract["<b>Job extraction</b><br/>[Flash-Lite, no tools]<br/>output: fixed schema only"]
        watch["<b>5 Watch</b><br/>[golden + attack evals,<br/>judges, alerts]"]
    end

    cap["<b>Spend cap</b><br/>[External: GCP budget, $25 a month]"]

    attacker -->|"message [HTTPS]"| edge
    edge -->|"within limits"| gate
    gate -->|"about Rayhan<br/>(most attacks are)"| answer
    gate -->|"off topic:<br/>fixed decline"| seen
    answer -->|"reply"| stamps
    stamps -->|"reply with a stamp<br/>on every sentence [SSE]"| seen
    jobpost -->|"text or URL"| extract
    extract -->|"title, level, skills,<br/>location, summary"| answer
    answer -->|"every call billed under"| cap
    watch -.->|"golden set on every push,<br/>alerts on live traffic"| answer

    subgraph key["Key"]
        k1["Layer in this system"]:::cont
        k2["External system"]:::ext
        k3[/"Untrusted input"/]:::data
        k4["solid arrow: runtime path<br/>dashed arrow: checks"]:::note
    end
    style key fill:none,stroke:#9ca3af,color:#6b7280
    style sys fill:none,stroke:#1d4f80,color:#6b7280,stroke-dasharray: 5 3

    classDef person fill:#0b3d6e,stroke:#0b3d6e,color:#ffffff
    classDef cont fill:#3b82c4,stroke:#1d4f80,color:#ffffff
    classDef data fill:#fef3c7,stroke:#b45309,color:#111827
    classDef ext fill:#e5e7eb,stroke:#6b7280,color:#111827
    classDef note fill:none,stroke:none,color:#6b7280
    class attacker,seen person
    class edge,gate,answer,stamps,extract,watch cont
    class jobpost data
    class cap ext
```

| Layer | What it stops | What gets past it, and what catches that |
|---|---|---|
| 1 Edge limits | floods, forged or reused sessions, one person using up the day's tokens | slow, spread-out abuse: the daily token budget, then the spend cap |
| 2 Off-topic gate | requests unrelated to Rayhan, plain jailbreaks ("You are now DAN") | attacks phrased as questions about him (6 of 7 on its holdout), by design: a topic filter must pass "did he get expelled?" |
| 3 Answer model and rules | on a holdout of 20 new attacks (2 replies each), it gave in on 1 of 40 replies, against 15 before 2.11.0 ([eval report §6](EVAL-REPORT.md#6-what-does-it-say-to-attacks-oct-3031-2026)) | prompt rules are not a security boundary; what's left to leak is the public profile and a prompt that is already public |
| 4 Stamps | unsupported sentences reach the visitor marked, not hidden | the verifier mislabels some true meta sentences and missed fabricated denials before 2.11.0; it needs a labelled set |
| 5 Watch | regressions: four attacks are in the golden set that gates every deploy | new attack styles: the attack test is re-run by hand, not scheduled |

---

## 13. Decisions

| Decision | Why | Trade-off |
|---|---|---|
| One `profile.json` for the site, the agent and the on-device tier | The site and the agent can't disagree | Every fact change is a commit and a redeploy |
| Code, not the model, decides `checked` | A model can quote what isn't there; a substring check can't | Paraphrased truths land as `inferred` |
| Stream first, stamp after | First words in seconds; verification adds no wait | Stamps arrive a moment after the text |
| A decision model as a second opinion, demote-only | It can only make a stamp more cautious | One more paid call per answer (fractions of a cent) |
| A decision model as the off-topic gate, seeing the previous turn; a failure lets the message through | Measured Oct 30: 96.7% vs 88.3% on held-out single messages and 180 vs 874 ms (version 2); then, with the previous turn, 0 of 33 real messages refused vs 7 on held-out conversations by another author (version 3, [eval report §5](EVAL-REPORT.md#5-should-it-answer-at-all-the-off-topic-gate-oct-30-2026)). The answer model declined every off-topic message it was tested on, so letting one through costs a cent, refusing a recruiter costs more | Visitor messages and the previous reply go to OpenRouter and TypeSafe (zero retention requested); two thresholds to keep calibrated, so a daily drift check re-scores 14 messages; more attacks phrased as questions about Rayhan reach the answer model |
| Attacks handled in the answer layer and the stamps, not by a stricter gate | Most attacks are about Rayhan, so a topic filter has to pass them; a stricter gate would refuse recruiters. With rules per attack type, the answer model gave in on 1 of 40 held-out replies, against 15 ([§12](#12-threats-and-defences)) | Prompt rules can be beaten by new phrasings; the structural layers (nothing to steal, no tools, schema-only job posts) are what bound the damage |
| API called directly, not through Cloudflare | Long SSE streams avoid proxy timeouts | Limits and sanitisation live in the app; the origin lock is planned |
| Raw async HTTP to Gemini, not the SDK | The SDK hung the server in production | Request shapes maintained by hand, covered by tests |
| Model IDs as settings, health probe makes a real call | "Configured" is not "working" | One cached model call every 10 minutes |
| Quotas counted in Postgres with an in-memory fallback | Limits survive restarts, and don't fall to zero if the database drops | A database read per message |
| Probes, not traffic, as the alert signal | A few visitors a day makes error rates noise | Probe traffic must be tagged and kept out of analytics and leads |
| A2 every 6 hours from GitHub, not hourly in Grafana | Each run is a real answer (about a cent); Grafana's longest interval is 1 hour | GitHub's schedule is unreliable, so A2 emails instead of paging |

More context: [system card](SYSTEM-CARD.md) (data, safety, evaluation, known failures), [monitoring design](MONITORING.md), [post-mortem](POSTMORTEM-2026-10-chatbot-outage.md).
