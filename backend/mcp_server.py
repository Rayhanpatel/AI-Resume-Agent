"""
MCP interface: the same agent, callable by other agents.

Mounted at /mcp on the FastAPI app (Streamable HTTP, stateless, JSON responses).
Three read-only tools over the public profile. No auth: the data is public and
the tools take no actions; the per-IP rate limit, per-IP daily limit and the
global daily token budget apply through `core/mcp_limits.py` (added Oct 25, 2026;
before that this docstring claimed limits that were not there). The web chat remains the only recruiter-facing interface; this
is the architecture (one agent, many hosts) and a demo for interviews.

Try it from Claude: add a custom connector with the URL https://<api-host>/mcp
"""
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from core.prompts import PROFILE
from core.mcp_limits import record_tool_tokens

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    "rayhan-patel-profile",
    title="Rayhan Patel's agent",
    instructions=(
        "Answers questions about Rayhan Patel, an AI engineer (M.S. Applied ML, University of Maryland, May 2027), "
        "from his public profile only. Use get_profile for structured facts, ask for a written answer with per-claim "
        "verification stamps, and fit_report to map his experience to a job description."
    ),
    website_url="https://rayhanpatel.com",
    version="2.8.0",
)


@mcp.tool(annotations=READ_ONLY, title="Get profile section")
def get_profile(section: str = "all") -> dict:
    """
    Return Rayhan Patel's public profile, or one section of it.
    Sections: basics, about, experience, projects, publications, honors, skills, education, faq, all.
    """
    if section == "all":
        return PROFILE
    if section in PROFILE:
        return {section: PROFILE[section], "profile_version": _version(), "as_of": PROFILE.get("meta", {}).get("updated")}
    return {"error": f"unknown section '{section}'", "sections": [k for k in PROFILE if k != "meta"]}


def _version() -> str:
    from services.verifier import PROFILE_VERSION
    return PROFILE_VERSION


def _start_trace(app_state, name: str, input_text: str):
    """A Langfuse trace for one MCP tool call, tagged "mcp" (and "synthetic" for probe traffic),
    so agents' questions get the same quality judges and the same 90-day deletion as web chats"""
    tracer = getattr(app_state, "tracing_service", None)
    if not tracer:
        return None, None
    from core import telemetry
    from services.tracer import trace_tags
    return tracer, tracer.trace(name=name, session_id="mcp", input=input_text,
                                tags=trace_tags("mcp", synthetic=telemetry.is_synthetic()))


async def _respond_traced(app_state, name: str, input_text: str, **kwargs) -> tuple:
    tracer, trace = _start_trace(app_state, name, input_text)
    try:
        result = await app_state.agent_service.respond(trace=trace, **kwargs)
    except Exception as e:
        if tracer:
            tracer.end_trace(trace, error=type(e).__name__)
        raise
    return tracer, trace, result


def attach_agent_tools(app_state):
    """Register the tools that need the running app's services (called from the app lifespan)"""

    @mcp.tool(annotations=READ_ONLY, title="Ask about Rayhan")
    async def ask(question: str, context: str = "") -> dict:
        """
        Ask a question about Rayhan Patel and get a written answer with verification stamps:
        every sentence is marked checked (quote verified in the profile), inferred, unknown or n/a.
        `context` may hold the asker's company or role for a tailored answer.
        """
        from services.verifier import verify_answer
        tracer, trace, result = await _respond_traced(
            app_state, "mcp_ask", question[:4000],
            session_id="mcp", query=question[:4000], user_name="an agent", company=(context or "")[:100] or None
        )
        record_tool_tokens(app_state, result)
        verification = await verify_answer(result["response"], app_state.verifier, getattr(app_state, "decision_gate", None))
        if tracer:
            tracer.end_trace(trace, output=result["response"], metadata={"tool": "ask", "stamps": verification.get("summary")})
        return {"answer": result["response"], "model": result.get("model"), "verification": verification}

    @mcp.tool(annotations=READ_ONLY, title="Fit report for a job")
    async def fit_report(job_description: str) -> dict:
        """
        Map Rayhan Patel's experience to a job description (paste the text, not a URL).
        Returns matched requirements, gaps the profile does not cover, and verification stamps.
        """
        from core.config import get_settings
        from services.job_parser import parse_job_description
        from services.verifier import verify_answer
        settings = get_settings()
        parsed = await parse_job_description(app_state.http_client, app_state.vertex_auth, settings.google_api_key,
                                             job_description[:15000], model=settings.gemini_fast_model)
        job_info = parsed.to_dict() if (parsed.company_name or parsed.role_title) else None
        question = ("Write a short fit report for this role: which requirements Rayhan's profile meets, with the "
                    "specific evidence, and which it does not cover. Be candid about gaps.")
        tracer, trace, result = await _respond_traced(
            app_state, "mcp_fit_report", job_description[:4000],
            session_id="mcp", query=question, user_name="an agent", company=(job_info or {}).get("company_name"), job_info=job_info
        )
        record_tool_tokens(app_state, result)
        verification = await verify_answer(result["response"], app_state.verifier, getattr(app_state, "decision_gate", None))
        if tracer:
            tracer.end_trace(trace, output=result["response"], metadata={"tool": "fit_report", "stamps": verification.get("summary")})
        return {"job": job_info, "report": result["response"], "verification": verification}


def build_asgi_app():
    """The ASGI sub-app to mount; the host lifespan must run `mcp.session_manager.run()`"""
    from mcp.server.transport_security import TransportSecuritySettings
    return mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
