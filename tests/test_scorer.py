import types

import httpx

from job_radar.models import Posting, Profile, Score
from job_radar.scorer import (build_prompt, parse_score, _extract_json, heuristic_score,
                              GeminiProvider, ClaudeProvider, FakeProvider,
                              HeuristicProvider, FallbackProvider, BedrockProvider)

PROFILE = Profile(summary="CS student", title_include=[], title_exclude=[],
                  locations_allow=[], locations_block=[], freshness_days=21)
POSTING = Posting(uid="x:1", ats="ashby", company="cohere", title="ML Intern",
                  location="Toronto", url="u", posted_at=None, description="Build models")


def test_build_prompt_includes_profile_and_posting():
    prompt = build_prompt(POSTING, PROFILE)
    assert "CS student" in prompt
    assert "ML Intern" in prompt
    assert "cohere" in prompt


def test_parse_score_clamps_and_defaults():
    good = {"candidates": [{"content": {"parts": [{"text": '{"score": 150, "reason": "great", "tags": ["ai"]}'}]}}]}
    s = parse_score(good)
    assert s.value == 100 and s.reason == "great" and s.tags == ["ai"]

    bad = {"nope": 1}
    s2 = parse_score(bad)
    assert s2.value == 0 and s2.ok is False  # unparseable -> flagged so it's not mirrored


async def test_gemini_http_error_marks_score_not_ok():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "quota exceeded"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        s = await GeminiProvider("KEY", client=client).score(POSTING, PROFILE)
    assert s.value == 0 and s.ok is False  # a 429 is an error, not a real zero fit


async def test_successful_score_is_ok():
    def handler(request):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": '{"score": 72, "reason": "fit", "tags": []}'}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        s = await GeminiProvider("KEY", client=client).score(POSTING, PROFILE)
    assert s.value == 72 and s.ok is True


async def test_fake_provider():
    s = await FakeProvider(value=88, reason="r").score(POSTING, PROFILE)
    assert s.value == 88


async def test_gemini_provider_posts_and_parses():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": '{"score": 72, "reason": "solid fit", "tags": ["intern","ai"]}'}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GeminiProvider("KEY", model="gemini-2.0-flash", client=client)
        s = await provider.score(POSTING, PROFILE)

    assert "generativelanguage.googleapis.com" in captured["url"]
    assert "key=KEY" in captured["url"]
    assert s.value == 72 and "solid fit" in s.reason


def test_extract_json_tolerates_fences_and_garbage():
    assert _extract_json('{"score": 50}')["score"] == 50
    assert _extract_json('```json\n{"score": 60}\n```')["score"] == 60
    assert _extract_json("here you go: {\"score\": 70} thanks")["score"] == 70
    assert _extract_json("not json at all") == {}


def test_extract_json_normalizes_non_dict_shapes():
    # LLMs sometimes wrap the object in an array or return a bare scalar.
    assert _extract_json('[{"score": 70, "reason": "x"}]') == {"score": 70, "reason": "x"}
    assert _extract_json("88") == {}        # bare scalar -> {}
    assert _extract_json('["a", "b"]') == {}  # array of non-dicts -> {}


def test_coerce_score_never_crashes_and_is_tolerant():
    from job_radar.scorer import _coerce_score
    # Non-dict shapes must yield a zero Score, never raise AttributeError.
    assert _coerce_score([{"score": 80}]).value == 0
    assert _coerce_score(88).value == 0
    assert _coerce_score("88").value == 0
    assert _coerce_score({}).value == 0
    # Tolerant numeric parsing.
    assert _coerce_score({"score": "80.5"}).value == 80
    assert _coerce_score({"score": "85%"}).value == 85
    assert _coerce_score({"score": 200}).value == 100  # clamped


def _post(title, desc=""):
    return Posting(uid="x:1", ats="ashby", company="c", title=title, location="Toronto",
                   url="u", posted_at=None, description=desc)


def test_heuristic_ranks_early_career_techy_above_senior():
    intern = heuristic_score(_post("Software Engineer Intern", "Python, React, AWS"), PROFILE)
    senior = heuristic_score(_post("Senior Staff Engineer", "Python"), PROFILE)
    plain = heuristic_score(_post("Data Analyst", ""), PROFILE)
    assert intern.value > plain.value > senior.value
    assert intern.ok is True and "heuristic" in intern.tags  # real, sortable, transparent
    assert 0 <= senior.value <= 100


async def test_fallback_uses_primary_when_ok_else_heuristic():
    class OkPrimary:
        async def score(self, p, prof):
            return Score(73, "llm", ok=True)

    class FailPrimary:
        async def score(self, p, prof):
            return Score(0, "LLM error: 429", ok=False)

    good = await FallbackProvider(OkPrimary(), HeuristicProvider()).score(
        _post("Software Engineer Intern"), PROFILE)
    assert good.value == 73 and "heuristic" not in good.tags  # primary score kept

    fell = await FallbackProvider(FailPrimary(), HeuristicProvider()).score(
        _post("Software Engineer Intern", "Python React"), PROFILE)
    assert fell.ok is True and "heuristic" in fell.tags  # degraded to heuristic, not zero


def test_build_provider_wraps_llm_and_falls_back_keyless():
    from job_radar.pipeline import build_provider
    keyless = build_provider(types.SimpleNamespace(llm_api_key=None, llm_provider="gemini", llm_model=""))
    assert isinstance(keyless, HeuristicProvider)
    withkey = build_provider(types.SimpleNamespace(llm_api_key="K", llm_provider="gemini", llm_model=""))
    assert isinstance(withkey, FallbackProvider)
    # Bedrock auths via the AWS chain, so it's selected even without an API key.
    bedrock = build_provider(types.SimpleNamespace(llm_api_key=None, llm_provider="bedrock", llm_model=""))
    assert isinstance(bedrock, FallbackProvider)


async def test_bedrock_provider_parses_converse_response():
    class FakeBedrock:
        def converse(self, **kw):
            self.kw = kw
            return {"output": {"message": {"content": [
                {"text": '{"score": 77, "reason": "solid match", "tags": ["ai"]}'}]}}}

    fb = FakeBedrock()
    s = await BedrockProvider("model-x", client=fb).score(POSTING, PROFILE)
    assert s.value == 77 and "solid match" in s.reason and s.ok is True
    assert fb.kw["modelId"] == "model-x"          # the configured model is used
    assert fb.kw["system"][0]["text"].startswith("You screen")  # instructions sent as system


async def test_bedrock_error_marks_not_ok():
    class BadBedrock:
        def converse(self, **kw):
            raise RuntimeError("AccessDeniedException")

    s = await BedrockProvider("m", client=BadBedrock()).score(POSTING, PROFILE)
    assert s.value == 0 and s.ok is False           # falls through to the heuristic in build_provider


async def test_claude_provider_posts_and_parses():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        captured["version"] = request.headers.get("anthropic-version")
        return httpx.Response(200, json={"content": [
            {"type": "text", "text": '{"score": 81, "reason": "great fit", "tags": ["ai","intern"]}'}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ClaudeProvider("KEY", model="claude-haiku-4-5", client=client)
        s = await provider.score(POSTING, PROFILE)

    assert "api.anthropic.com/v1/messages" in captured["url"]
    assert captured["api_key"] == "KEY"
    assert captured["version"] == "2023-06-01"
    assert s.value == 81 and "great fit" in s.reason and "ai" in s.tags
