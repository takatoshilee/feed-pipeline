import httpx

from job_radar.models import Posting, Profile
from job_radar.scorer import (build_prompt, parse_score, _extract_json,
                              GeminiProvider, ClaudeProvider, FakeProvider)

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
    assert s2.value == 0


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
