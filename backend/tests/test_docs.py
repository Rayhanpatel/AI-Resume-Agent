"""
The docs must not drift from the code. In Oct 2026 the README's diagrams had fallen a release
behind (no stamps, no MCP, no monitoring, a wrong ID type) and nothing noticed. These tests read
the source and fail when a route, an event type, an MCP tool or a fallback timeout is missing from
docs/REFERENCE.md (the API table), the README or docs/ARCHITECTURE.md.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
REFERENCE = (ROOT / "docs" / "REFERENCE.md").read_text(encoding="utf-8")
ARCH = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")


def _source(path: str) -> str:
    return (BACKEND / path).read_text(encoding="utf-8")


def _backend_files():
    return [p for p in BACKEND.rglob("*.py") if not {"venv", "tests"} & set(p.parts)]


def test_every_route_is_in_the_reference_api_table():
    routes = set()
    for f in (BACKEND / "api" / "routes").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        prefix = re.search(r'APIRouter\(prefix="([^"]+)"', text)
        for path in re.findall(r'@router\.(?:get|post|put|delete|patch)\("([^"]+)"', text):
            routes.add("/api/v1" + (prefix.group(1) if prefix else "") + path)
    assert len(routes) >= 8, routes
    missing = sorted(r for r in routes if f"`{r}`" not in REFERENCE)
    assert not missing, f"routes missing from the API table in docs/REFERENCE.md: {missing}"


def test_every_mcp_tool_is_documented():
    tools = set(re.findall(r"@mcp\.tool\([^)]*\)\s*\n\s*(?:async )?def (\w+)", _source("mcp_server.py")))
    assert tools >= {"ask", "fit_report", "get_profile"}, tools
    missing = sorted(t for t in tools if f"`{t}`" not in README or f"`{t}`" not in REFERENCE)
    assert not missing, f"MCP tools missing from the README or docs/REFERENCE.md: {missing}"


def test_every_event_type_is_in_the_architecture_data_model():
    types = set()
    for f in _backend_files():
        types |= set(re.findall(r'event_type="([a-z_]+)"', f.read_text(encoding="utf-8")))
    assert {"session_start", "chat_stream", "mcp"} <= types, types
    missing = sorted(t for t in types if t not in ARCH)
    assert not missing, f"event types missing from docs/ARCHITECTURE.md section 9: {missing}"


def test_fallback_timeouts_match_the_state_machine():
    first = float(re.search(r"gemini_first_token_timeout: float = ([\d.]+)", _source("core/config.py")).group(1))
    agent = _source("services/agent.py")
    assert "first_token_timeout * (1 + i * 0.5)" in agent and "45.0" in agent, "the fallback schedule changed"
    for seconds in (first, first * 1.5, 45):
        assert f"first token within {seconds:g} s" in ARCH, f"section 6 does not show the {seconds:g} s step"
