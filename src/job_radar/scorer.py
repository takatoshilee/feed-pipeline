import json

import httpx

from .models import Posting, Profile, Score

# --- shared prompt pieces (provider-neutral) ---

INSTRUCTIONS = (
    "You screen internship / new-grad postings for ONE specific candidate (described "
    "below). Read the role's actual requirements and qualifications, then score 0-100 by "
    "judging TWO things together:\n"
    "1) REALISTIC CHANCE: would this candidate plausibly be competitive? Compare the "
    "stated requirements to the candidate's real skills and year in school. Heavily "
    "penalize roles that need a domain or skill the candidate does NOT list (e.g. "
    "Android/iOS/mobile, embedded/firmware/hardware, kernel, game/graphics, a specific "
    "language they never mention), senior/staff/manager titles, security clearance, or "
    "several years of experience. A role titled 'Software Engineer Intern' in a domain "
    "they have no background in is a WEAK match, not a strong one, even though the title "
    "looks right.\n"
    "2) GOOD FOR THEM: reward strong overlap with the candidate's listed strengths and "
    "target roles (SWE / AI-ML / data). Give PARTIAL credit to sensible tangential "
    "stretches that still use those strengths (e.g. a data or full-stack role at an AI "
    "company). Be calibrated: most postings should land 30-70; reserve 80+ for genuinely "
    "strong, realistic matches.\n"
    "Respond as JSON {score, reason, tags}; reason = one short sentence naming the key fit "
    "or gap; tags = a few lowercase labels."
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


def _as_dict(obj) -> dict:
    """Normalize a parsed JSON value to a dict. LLMs sometimes wrap the object in a
    single-element array or return a bare scalar; anything non-dict-shaped -> {}."""
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj[0]
    return {}


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of an LLM text response, tolerating ``` fences.
    Always returns a dict (never a list/scalar), so callers can safely .get()."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if "\n" in t:
            first, rest = t.split("\n", 1)
            t = rest if first.strip().lower() in ("json", "") else t
    try:
        return _as_dict(json.loads(t))
    except (json.JSONDecodeError, TypeError):
        i, j = t.find("{"), t.rfind("}")
        if 0 <= i < j:
            try:
                return _as_dict(json.loads(t[i:j + 1]))
            except json.JSONDecodeError:
                return {}
        return {}


def _coerce_score(obj) -> Score:
    if not isinstance(obj, dict) or not obj:
        return Score(value=0, reason="unparseable LLM response", tags=[], ok=False)
    try:
        # Tolerate ints, int-strings, floats, and percentages like "85%" / "80.5".
        val = int(round(float(str(obj.get("score", 0)).strip().rstrip("%") or 0)))
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
        return Score(value=0, reason="unparseable LLM response", tags=[], ok=False)
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
            return Score(value=0, reason=f"LLM error: {e!r}"[:200], tags=[], ok=False)
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
            return Score(value=0, reason=f"LLM error: {e!r}"[:200], tags=[], ok=False)
        finally:
            if owns:
                await client.aclose()
        return _coerce_score(_extract_json(text))


# --- Heuristic fallback (deterministic, no network) ---

_EARLY = ("intern", "internship", "new grad", "new-grad", "new graduate", "co-op", "co op",
          "coop", "junior", "early career", "early-career", "graduate", "entry level",
          "entry-level", "student", "apprentice", "university")
_SENIOR = ("senior", "sr.", "staff", "principal", " lead", "manager", "director", "head of",
           "vp ", "architect", " ii", " iii", " iv", "distinguished", "expert", "10+")
_CORE = ("software", "developer", "engineer", "swe", "backend", "back end", "back-end",
         "frontend", "front end", "full stack", "full-stack", "machine learning", " ml",
         " ai", "data", "platform", "infrastructure", " web")
_TECH = ("python", "java", "javascript", "typescript", "react", "node", "golang", "rust",
         "c++", "sql", "aws", "gcp", "azure", "docker", "kubernetes", "pytorch", "tensorflow",
         "llm", "nlp", "api", "fastapi", "django", "flask", "next.js", "postgres", "spark")
# Domains the candidate has NO background in: a title in one of these is a weak match even
# if it says "Software Engineer Intern". (Tracks this candidate's gaps; the LLM judges this
# properly from the description -- this just keeps the offline fallback from over-scoring.)
_MISMATCH = ("android", "ios", "mobile", "embedded", "firmware", "hardware", "fpga",
             "verilog", "rtl", "asic", "kernel", "device driver", "mechanical", "electrical",
             "analog", "silicon", "photonics", "rf ", "game", "graphics", "rendering",
             "unreal engine", "shader")


def heuristic_score(posting: Posting, profile: Profile) -> Score:
    """Deterministic 0-100 fit estimate from title and skill signals, for when the LLM is
    unavailable (rate-limited or no key). Lower precision than the LLM, but it keeps the
    radar useful: a rules-survivor still gets a sensible, sortable score instead of nothing.
    Marked ok=True (it's a real score) and tagged 'heuristic' so it's transparent."""
    title = (posting.title or "").lower()
    text = f"{title} {(posting.description or '')[:1500].lower()}"
    # Baseline sits just under digest_threshold (50): a generic full-time rules-survivor
    # lands ~45-53, while an early-career and/or techy role clears comfortably. This keeps
    # a heuristic-scored backfill focused on intern/co-op/new-grad roles, not everything.
    score = 40

    if any(s in title for s in _SENIOR):
        score -= 30
    if any(e in title for e in _EARLY):
        score += 28
    if any(c in title for c in _CORE):
        score += 8
    score += min(12, sum(3 for t in _TECH if t in text))
    if any(m in title for m in _MISMATCH):  # specialized domain the candidate lacks
        score -= 22

    score = max(0, min(100, score))
    return Score(value=score, reason="heuristic (LLM unavailable): title + skill match",
                 tags=["heuristic"], ok=True)


class HeuristicProvider:
    """Scores deterministically, no network. Used keyless and as the LLM fallback."""

    async def score(self, posting: Posting, profile: Profile) -> Score:
        return heuristic_score(posting, profile)


class FallbackProvider:
    """Try the primary (LLM) provider; if it errors (score.ok is False, e.g. a 429), fall
    back to the deterministic heuristic so an LLM outage degrades to lower-precision
    coverage instead of zero coverage."""

    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback

    async def score(self, posting: Posting, profile: Profile) -> Score:
        s = await self.primary.score(posting, profile)
        return s if s.ok else await self.fallback.score(posting, profile)


class FakeProvider:
    """Constant-score provider for tests and keyless smoke runs."""

    def __init__(self, value: int = 70, reason: str = "fake", tags=None):
        self.value = value
        self.reason = reason
        self.tags = tags or []

    async def score(self, posting: Posting, profile: Profile) -> Score:
        return Score(self.value, self.reason, list(self.tags))
