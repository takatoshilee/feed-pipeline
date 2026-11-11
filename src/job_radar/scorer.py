import asyncio
import json

import httpx

from .models import Posting, Profile, Score

# --- shared prompt pieces (provider-neutral) ---

INSTRUCTIONS = (
    "You screen job postings for ONE specific candidate (described below). Read the whole "
    "posting, then answer a few separate questions about it. Do NOT give an overall score; "
    "the overall score is computed from your answers, so answer each one on its own.\n"
    "- student_role (true/false): is this an internship, co-op, placement, or otherwise "
    "explicitly open to current undergraduates? A plain full-time title with no student "
    "wording is false.\n"
    "- blocker: \"\" if none, else ONE of \"grad_degree\" (requires a Master's/PhD), "
    "\"citizenship\" (US citizenship, clearance, permanent residency, or explicitly no visa "
    "sponsorship), \"past_term\" (the work term starts before the earliest start the "
    "candidate profile allows), \"senior\" (needs 3+ years or a senior/staff/manager level).\n"
    "- requirements_met (0-10): how many of the posting's stated must-have requirements the "
    "candidate meets today, judged only from the skills and experience in the profile. 10 = "
    "meets all, 5 = about half, 0 = none. Missing a core domain skill (e.g. a language, "
    "tool or field they never list) costs a lot.\n"
    "- interest (0-10): how well the role's day-to-day work matches the candidate's target "
    "lanes and preferences in the profile. 10 = exactly what they want, 5 = acceptable, "
    "0 = work they said they don't want.\n"
    "- lane: the role family, one of \"data\", \"ml\", \"product\", \"marketing\", "
    "\"design\", \"swe\", \"other\".\n"
    "- low_level (true/false): is the core work compilers, kernels, drivers, embedded, "
    "firmware, hardware or chip design, or low-level performance tuning? Judge from the "
    "duties, not the company.\n"
    "Use the full 0-10 range and different values for different postings; avoid giving "
    "every decent posting the same numbers.\n"
    "Respond as JSON {student_role, blocker, requirements_met, interest, lane, low_level, "
    "reason, tags, resume, term}; reason = one short sentence naming the key fit or gap; "
    "tags = a few lowercase labels; resume = which of the candidate's two resumes is closer: "
    "\"ai\" for AI / ML / data-science / analytics / research roles, \"swe\" for software, "
    "product, design or other roles; term = the work-term timing EXACTLY as the posting states "
    "it, season + year and start/end or length if given (e.g. \"Summer 2027 (May-Aug)\", "
    "\"Fall 2027, 4 months\", \"16-month, May 2027-Aug 2028\"); use \"not stated\" if the "
    "posting gives no dates or season."
)

DESC_CHARS = 6000   # how much of the posting the LLM reads (term and eligibility sit low down)
LANES = ("data", "ml", "product", "marketing", "design", "swe", "other")
BLOCKERS = ("grad_degree", "citizenship", "past_term", "senior")


def compute_fit(student_role: bool, blocker: str, requirements_met: float,
                interest: float, low_level: bool) -> int:
    """The overall 0-100 fit, from the LLM's separate answers. Chance and interest weigh
    the same (each 0-10 -> up to 45 points). A non-student role or a hard blocker caps
    the score at 30 (still visible, never pinged); low-level work loses 15."""
    req = max(0.0, min(10.0, float(requirements_met)))
    want = max(0.0, min(10.0, float(interest)))
    fit = 5 + 4.5 * req + 4.5 * want
    if low_level:
        fit -= 15
    if not student_role or blocker:
        fit = min(fit, 30)
    return int(round(max(0, min(100, fit))))


def _candidate_block(profile: Profile) -> str:
    return f"CANDIDATE:\n{profile.summary}"


def _posting_block(posting: Posting) -> str:
    desc = (posting.description or "")[:DESC_CHARS]
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


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1")


def _coerce_score(obj) -> Score:
    if not isinstance(obj, dict) or not obj:
        return Score(value=0, reason="unparseable LLM response", tags=[], ok=False)
    reason = str(obj.get("reason", ""))[:300]
    tags = [str(t) for t in obj.get("tags", []) if isinstance(t, (str, int))][:8]
    resume = str(obj.get("resume", "")).strip().lower()
    resume = resume if resume in ("ai", "swe") else ""
    term = str(obj.get("term", "")).strip()[:80]
    lane = str(obj.get("lane", "")).strip().lower()
    lane = lane if lane in LANES else ""
    flags = []
    if "requirements_met" in obj or "interest" in obj:
        blocker = str(obj.get("blocker", "") or "").strip().lower()
        blocker = blocker if blocker in BLOCKERS else ""
        low = _bool(obj.get("low_level", False))
        student = _bool(obj.get("student_role", True))
        val = compute_fit(student, blocker, _num(obj.get("requirements_met")),
                          _num(obj.get("interest")), low)
        if low:
            flags.append("low-level")
        if blocker:
            flags.append(blocker.replace("_", " "))
        if not student:
            flags.append("not a student role")
    else:
        # Older single-number answer (kept so a provider that ignores the new fields
        # still yields a usable score).
        val = int(round(_num(obj.get("score", 0))))
    val = max(0, min(100, val))
    return Score(value=val, reason=reason, tags=tags, resume=resume, term=term,
                 lane=lane, flags=flags)


# --- Gemini (REST) ---

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "student_role": {"type": "BOOLEAN"},
        "blocker": {"type": "STRING"},
        "requirements_met": {"type": "INTEGER"},
        "interest": {"type": "INTEGER"},
        "lane": {"type": "STRING"},
        "low_level": {"type": "BOOLEAN"},
        "reason": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "resume": {"type": "STRING"},
        "term": {"type": "STRING"},
    },
    "required": ["student_role", "blocker", "requirements_met", "interest", "lane",
                 "low_level", "reason", "tags", "resume", "term"],
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
            "max_tokens": 500,
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


# --- AWS Bedrock (Claude via the Converse API) ---

class BedrockProvider:
    """Scores via Amazon Bedrock's Converse API (model-agnostic; defaults to a Claude).
    Uses boto3 (lazy import) so AWS SigV4 auth and the default credential chain are handled
    for us: set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION (or an instance role).
    Bedrock isn't on Gemini's tiny free-tier daily quota, so it's the durable scorer."""

    def __init__(self, model: str = "anthropic.claude-3-5-haiku-20241022-v1:0",
                 region: str | None = None, client=None):
        self.model = model
        self.region = region
        self._client = client

    def _bedrock(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    async def score(self, posting: Posting, profile: Profile) -> Score:
        system = [{"text": f"{INSTRUCTIONS}\n\n{_candidate_block(profile)}"}]
        messages = [{"role": "user", "content": [{"text": _posting_block(posting)}]}]

        def call():
            return self._bedrock().converse(
                modelId=self.model, system=system, messages=messages,
                inferenceConfig={"maxTokens": 500, "temperature": 0.2})

        try:
            resp = await asyncio.to_thread(call)
            text = resp["output"]["message"]["content"][0]["text"]
        except Exception as e:
            return Score(value=0, reason=f"LLM error: {e!r}"[:200], tags=[], ok=False)
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


HEURISTIC_TAG = "heuristic"


def is_ai_score(score: Score) -> bool:
    """True for a real LLM score. A keyword-backup score is good for ordering a queue but
    is never written to the Sheet or pinged: the daily sweep scores that role properly."""
    return score.ok and HEURISTIC_TAG not in (score.tags or [])


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
                 tags=[HEURISTIC_TAG], ok=True)


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
