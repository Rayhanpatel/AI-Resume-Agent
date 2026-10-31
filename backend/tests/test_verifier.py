"""
Verification stamps: sentence splitting, the code-side quote and number checks,
status mapping from model verdicts, and the decision-gate demotion. No network.
"""
import asyncio

from services import verifier
from services.verifier import split_sentences, quote_in_profile, unverified_numbers, verify_answer, PROFILE_VERSION


def test_split_sentences_handles_markdown_and_bullets():
    answer = ("Rayhan **built** a memory service at 3E. It runs on Lambda!\n"
              "- First bullet with a [link](https://example.com) inside.\n"
              "- Short.\n"
              "Email rayhanbp@umd.edu for more?")
    parts = split_sentences(answer)
    assert parts[0] == "Rayhan built a memory service at 3E."
    assert parts[1] == "It runs on Lambda!"
    assert parts[2] == "First bullet with a link inside."
    assert "Short." not in parts  # below the minimum claim length
    assert parts[-1].startswith("Email rayhanbp@umd.edu")


def test_quote_check_is_whitespace_and_case_tolerant():
    assert quote_in_profile("AWS Lambda and DynamoDB")
    assert quote_in_profile("aws   lambda and\n dynamodb")
    assert not quote_in_profile("built the memory systems himself")
    assert not quote_in_profile("short")


def test_numbers_must_appear_in_the_profile():
    assert unverified_numbers("The best run was +0.0057 NDCG@10 on 59,902 queries.") == []
    assert unverified_numbers("He wrote about 125 conformance tests in 2026.") == []
    missing = unverified_numbers("He saved 42% and placed 3rd of 999 teams.")
    assert "42%" in missing and "999" in missing


class _FakeVerifier:
    def __init__(self, verdicts): self._v = verdicts
    async def verdicts(self, sentences): return self._v


class _FakeGate:
    model = "fake-decisions"
    def __init__(self, probs): self._p = probs
    async def probabilities(self, sentences): return self._p


def test_status_mapping_from_verdicts():
    answer = ("He built a multi-tenant agent-memory MCP service on AWS Lambda and DynamoDB. "
              "That makes him a strong fit for infrastructure roles. "
              "He has deployed Kubernetes clusters at scale. "
              "Feel free to email rayhanbp@umd.edu with questions.")
    verdicts = [
        {"i": 0, "verdict": "supported", "quote": "multi-tenant memory MCP service on AWS Lambda and DynamoDB", "path": "experience[0].highlights[0]"},
        {"i": 1, "verdict": "inferred", "quote": None, "path": None},
        {"i": 2, "verdict": "not_in_profile", "quote": None, "path": None},
        {"i": 3, "verdict": "not_a_claim", "quote": None, "path": None},
    ]
    result = asyncio.run(verify_answer(answer, _FakeVerifier(verdicts)))
    statuses = [c["status"] for c in result["claims"]]
    assert statuses == ["checked", "inferred", "unknown", "n/a"]
    assert result["claims"][0]["evidence"]["verified"] is True
    assert result["summary"] == {"checked": 1, "inferred": 1, "unknown": 1, "numbers_unverified": 0, "unverified": 0}
    assert result["profile_version"] == PROFILE_VERSION and result["gate_model"] is None


def test_supported_without_a_real_quote_is_only_inferred_and_bad_numbers_are_flagged():
    answer = "He cut latency by 73% at Euler AI."
    verdicts = [{"i": 0, "verdict": "supported", "quote": "this text is not in the profile at all", "path": "x"}]
    result = asyncio.run(verify_answer(answer, _FakeVerifier(verdicts)))
    claim = result["claims"][0]
    assert claim["status"] == "inferred" and claim["evidence"]["verified"] is False
    assert claim["numbers_unverified"] == ["73%"]
    assert result["summary"]["numbers_unverified"] == 1


def test_decision_gate_demotes_a_checked_claim_it_rejects():
    answer = "He built a multi-tenant agent-memory MCP service on AWS Lambda and DynamoDB."
    verdicts = [{"i": 0, "verdict": "supported", "quote": "memory MCP service on AWS Lambda and DynamoDB", "path": "p"}]
    kept = asyncio.run(verify_answer(answer, _FakeVerifier(verdicts), _FakeGate({0: 0.93})))
    demoted = asyncio.run(verify_answer(answer, _FakeVerifier(verdicts), _FakeGate({0: 0.2})))
    assert kept["claims"][0]["status"] == "checked" and kept["claims"][0]["gate_probability"] == 0.93
    assert demoted["claims"][0]["status"] == "inferred" and demoted["gate_model"] == "fake-decisions"


def test_verifier_failure_yields_unverified_not_an_exception():
    class _Broken:
        async def verdicts(self, sentences): raise RuntimeError("down")
    result = asyncio.run(verify_answer("He built something important at 3E.", _Broken()))
    assert result["claims"][0]["status"] == "unverified" and result["summary"]["unverified"] == 1



def test_split_sentences_does_not_break_at_degree_abbreviations():
    answer = ("He completed his B.Tech. in Computer Science at B.S. Abdur Rahman Crescent Institute in Chennai. "
              "He is finishing his M.S. Applied Machine Learning degree at the University of Maryland. It ends in May 2027.")
    assert split_sentences(answer) == [
        "He completed his B.Tech. in Computer Science at B.S. Abdur Rahman Crescent Institute in Chennai.",
        "He is finishing his M.S. Applied Machine Learning degree at the University of Maryland.",
        "It ends in May 2027."]


def test_a_sentence_that_speaks_for_the_profile_needs_a_real_quote():
    # Found Oct 30: to a cheating rumor the bot said "It states that Rayhan is currently in good academic standing",
    # which the profile never says, and the stamp was "inferred"
    answer = ("It states that Rayhan is currently in good academic standing at Maryland. "
              "The profile states that he built a multi-tenant agent-memory MCP service on AWS Lambda and DynamoDB. "
              "The profile shows he built a memory service on Lambda and DynamoDB for agents. "
              "He works on agent memory and evaluation.")
    verdicts = [
        {"i": 0, "verdict": "inferred", "quote": None, "path": None},
        {"i": 1, "verdict": "supported", "quote": "multi-tenant memory MCP service on AWS Lambda and DynamoDB", "path": "x"},
        # a true paraphrase anchored to a real quote stays "inferred"
        {"i": 2, "verdict": "inferred", "quote": "multi-tenant memory MCP service on AWS Lambda and DynamoDB", "path": "x"},
        {"i": 3, "verdict": "inferred", "quote": None, "path": None},
    ]
    statuses = [c["status"] for c in asyncio.run(verify_answer(answer, _FakeVerifier(verdicts)))["claims"]]
    assert statuses == ["unknown", "checked", "inferred", "inferred"]


def test_verifier_prompt_treats_refusals_and_absences_as_not_claims():
    prompt = verifier.VERIFY_PROMPT
    assert "what this assistant can or cannot do" in prompt
    assert "the profile has no information on something" in prompt
    assert "supported\" only if the profile states exactly that" in prompt
