from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


@dataclass(frozen=True)
class Posting:
    uid: str                      # stable global id: f"{ats}:{company}:{native_id}"
    ats: str                      # "greenhouse" | "lever" | "ashby"
    company: str
    title: str
    location: str
    url: str
    posted_at: datetime | None    # best-effort; None if ATS doesn't expose it
    description: str
    raw: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class Company:
    slug: str
    ats: str
    tier: str = "target"          # "dream" | "target"
    wd_host: str | None = None    # workday only (M2)
    wd_site: str | None = None    # workday only (M2)


class Urgency(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Score:
    value: int                    # 0-100
    reason: str
    tags: list[str] = field(default_factory=list, compare=False)
    ok: bool = field(default=True, compare=False)  # False => scoring errored (value is not a real fit)


@dataclass(frozen=True)
class Profile:
    summary: str                  # free-text candidate description for the LLM
    title_include: list[str]
    title_exclude: list[str]
    locations_allow: list[str]
    locations_block: list[str]
    freshness_days: int
    ping_threshold: int = 65
    digest_threshold: int = 50
    high_score: int = 80
    high_fresh_hours: int = 2
