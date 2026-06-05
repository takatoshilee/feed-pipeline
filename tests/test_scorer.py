import httpx

from job_radar.models import Posting, Profile
from job_radar.scorer import build_prompt, parse_score, GeminiProvider, FakeProvider

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
