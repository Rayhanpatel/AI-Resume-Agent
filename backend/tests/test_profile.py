"""
Content tests: the agent's knowledge must stay true.
The profile is public-safe by design; these tests stop known-wrong or private
wording from creeping back in through a careless edit or sync.
"""
import re

import pytest

from core.prompts import PROFILE, PROFILE_TEXT, get_system_prompt
from services.agent import AgentService

# Each pattern is wording that was wrong or private in an earlier version.
BANNED = [
    (r"\$\s?5,?000|\$5K|\b5K\b", "the hackathon prize issued was $1,000"),
    (r"top[- ]10 finalist", "no written confirmation of a top-10 placement"),
    (r"10 (backend )?services|10 microservices", "it is 10 modules in one deployment"),
    (r"\bCPT\b|Summer 2026", "internship-era work authorization is stale"),
    (r"College Park, MD", "public location is the Bay Area"),
    (r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b", "no phone number"),
    (r"built on (AWS )?(Bedrock )?AgentCore", "AgentCore was benchmarked, not the platform"),
    (r"110,?000|110K|first-time-fix", "unsourced statistics"),
    (r"80% of medical bills", "unsourced statistic"),
    (r"\bbrother\b|\bsister\b|\bfather\b|\bmother\b", "no family details"),
    (r"thousands of readers", "no blog or series exists"),
]


@pytest.mark.parametrize("pattern,reason", BANNED)
def test_profile_has_no_banned_claims(pattern, reason):
    hits = [m.group(0) for m in re.finditer(pattern, PROFILE_TEXT, flags=re.IGNORECASE)]
    assert not hits, f"{reason}: found {hits}"


@pytest.mark.parametrize("pattern,reason", BANNED)
def test_system_prompt_has_no_banned_claims(pattern, reason):
    prompt = get_system_prompt("Dana", "Acme", "Senior ML Engineer, LLM evaluation")
    hits = [m.group(0) for m in re.finditer(pattern, prompt, flags=re.IGNORECASE)]
    assert not hits, f"{reason}: found {hits}"


def test_profile_answers_the_questions_recruiters_always_ask():
    faq = PROFILE["faq"]
    assert "OPT" in faq["work_authorization"] and "June 2027" in faq["work_authorization"]
    assert "June 2027" in faq["availability"]
    for key in ("location", "compensation", "contact"):
        assert faq[key]


def test_profile_has_current_experience_and_research():
    companies = [job["company"] for job in PROFILE["experience"]]
    assert companies[0] == "3E" and "Euler AI" in companies
    assert any("EvoRank" in pub["title"] for pub in PROFILE["publications"])


def test_history_is_alternating_and_ends_with_the_question():
    history = [
        {"role": "assistant", "content": "Hi Dana! Ask me anything."},  # welcome message, dropped
        {"role": "user", "content": "What did he do at 3E?"},
        {"role": "assistant", "content": "He built an agent-memory MCP service."},
        {"role": "user", "content": "Unanswered retry"},
    ]
    contents = AgentService._history_contents(history, "Tell me more about that")
    roles = [c["role"] for c in contents]
    assert roles == ["user", "model", "user"]
    assert contents[-1]["parts"][0]["text"].endswith("Tell me more about that")


def test_history_is_capped():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(40)]
    contents = AgentService._history_contents(history, "latest")
    assert len(contents) <= 11
    assert contents[-1]["parts"][0]["text"].endswith("latest")
