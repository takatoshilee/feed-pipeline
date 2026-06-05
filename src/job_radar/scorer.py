import json

import httpx

from .models import Posting, Profile, Score

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER"},
        "reason": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["score", "reason", "tags"],
}


def build_prompt(posting: Posting, profile: Profile) -> str:
    desc = (posting.description or "")[:1500]
    return (
        "You are screening job postings for one specific candidate. Rate how well this "
        "posting fits THIS candidate from 0 (no fit) to 100 (perfect fit). The candidate "
        "is a student / early-career; penalize senior, staff, and manager roles and anything "
        "needing several years of experience. Consider role type, seniority, location, and "
        "skill overlap. Respond as JSON {score, reason, tags}; reason is one short sentence; "
        "tags is a short list of lowercase labels.\n\n"
        f"CANDIDATE:\n{profile.summary}\n\n"
        f"POSTING:\nCompany: {posting.company}\nTitle: {posting.title}\n"
        f"Location: {posting.location}\nDescription: {desc}\n"
    )


def parse_score(data: dict) -> Score:
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        obj = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return Score(value=0, reason="unparseable LLM response", tags=[])
    try:
        val = int(obj.get("score", 0))
    except (TypeError, ValueError):
        val = 0
    val = max(0, min(100, val))
    reason = str(obj.get("reason", ""))[:300]
    tags = [str(t) for t in obj.get("tags", []) if isinstance(t, (str, int))][:8]
    return Score(value=val, reason=reason, tags=tags)


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", client=None):
        self.api_key = api_key
        self.model = model
        self.client = client

    async def score(self, posting: Posting, profile: Profile) -> Score:
        body = {
            "contents": [{"parts": [{"text": build_prompt(posting, profile)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": SCHEMA,
                "temperature": 0.2,
            },
        }
        url = GEMINI_URL.format(model=self.model, key=self.api_key)
        owns = self.client is None
        client = self.client or httpx.AsyncClient(timeout=30.0)
        try:
            r = await client.post(url, json=body)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # never let a scoring error abort the run
            return Score(value=0, reason=f"LLM error: {e!r}"[:200], tags=[])
        finally:
            if owns:
                await client.aclose()
        return parse_score(data)


class FakeProvider:
    """Deterministic provider for tests and keyless local runs."""

    def __init__(self, value: int = 70, reason: str = "fake", tags=None):
        self.value = value
        self.reason = reason
        self.tags = tags or []

    async def score(self, posting: Posting, profile: Profile) -> Score:
        return Score(self.value, self.reason, list(self.tags))
