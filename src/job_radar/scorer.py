import json

import httpx

from .models import Posting, Profile, Score

# --- shared prompt pieces (provider-neutral) ---

INSTRUCTIONS = (
    "You are screening job postings for one specific candidate. Rate how well this "
    "posting fits THIS candidate from 0 (no fit) to 100 (perfect fit). The candidate "
    "is a student / early-career; penalize senior, staff, and manager roles and anything "
    "needing several years of experience. Consider role type, seniority, location, and "
    "skill overlap. Respond as JSON {score, reason, tags}; reason is one short sentence; "
    "tags is a short list of lowercase labels."
)


def _candidate_block(profile: Profile) -> str:
    return f"CANDIDATE:\n{profile.summary}"


def _posting_block(posting: Posting) -> str:
    desc = (posting.description or "")[:1500]
    return (f"POSTING:\nCompany: {posting.company}\nTitle: {posting.title}\n"
            f"Location: {posting.location}\nDescription: {desc}")


def build_prompt(posting: Posting, profile: Profile) -> str:
    """Single-string prompt (used by Gemini)."""
    return f"{INSTRUCTIONS}\n\n{_candidate_block(profile)}\n\n{_posting_block(posting)}\n"


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of an LLM text response, tolerating ``` fences."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if "\n" in t:
            first, rest = t.split("\n", 1)
            t = rest if first.strip().lower() in ("json", "") else t
    try:
        return json.loads(t)
    except (json.JSONDecodeError, TypeError):
        i, j = t.find("{"), t.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                return {}
        return {}


def _coerce_score(obj: dict) -> Score:
    if not obj:
        return Score(value=0, reason="unparseable LLM response", tags=[])
    try:
        val = int(obj.get("score", 0))
    except (TypeError, ValueError):
        val = 0
    val = max(0, min(100, val))
    reason = str(obj.get("reason", ""))[:300]
    tags = [str(t) for t in obj.get("tags", []) if isinstance(t, (str, int))][:8]
    return Score(value=val, reason=reason, tags=tags)


# --- Gemini (REST) ---

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER"},
        "reason": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["score", "reason", "tags"],
}


def parse_score(data: dict) -> Score:
    """Parse a Gemini generateContent response into a Score."""
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return Score(value=0, reason="unparseable LLM response", tags=[])
    return _coerce_score(_extract_json(text))


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
                "responseSchema": GEMINI_SCHEMA,
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


# --- Claude (Anthropic Messages API, via httpx to match the SDK-free design) ---

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class ClaudeProvider:
    """Scores via the Anthropic Messages API. Uses httpx (not the SDK) to stay
    consistent with this project's dependency-light, mockable design. Default model
    is Haiku 4.5 (high-volume classification); override via LLM_MODEL."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5", client=None):
        self.api_key = api_key
        self.model = model
        self.client = client

    async def score(self, posting: Posting, profile: Profile) -> Score:
        # Stable instructions + candidate go in `system` (cacheable prefix); the
        # volatile posting goes in the user turn. Caching engages once the prefix
        # exceeds the model minimum; harmless below it.
        system = [{
            "type": "text",
            "text": f"{INSTRUCTIONS}\n\n{_candidate_block(profile)}",
            "cache_control": {"type": "ephemeral"},
        }]
        body = {
            "model": self.model,
            "max_tokens": 300,
            "system": system,
            "messages": [{"role": "user", "content": _posting_block(posting)}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        owns = self.client is None
        client = self.client or httpx.AsyncClient(timeout=30.0)
        try:
            r = await client.post(ANTHROPIC_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            text = data["content"][0]["text"]
        except Exception as e:
            return Score(value=0, reason=f"LLM error: {e!r}"[:200], tags=[])
        finally:
            if owns:
                await client.aclose()
        return _coerce_score(_extract_json(text))


class FakeProvider:
    """Deterministic provider for tests and keyless local runs."""

    def __init__(self, value: int = 70, reason: str = "fake", tags=None):
        self.value = value
        self.reason = reason
        self.tags = tags or []

    async def score(self, posting: Posting, profile: Profile) -> Score:
        return Score(self.value, self.reason, list(self.tags))
