import os
from dataclasses import dataclass

import yaml

from .models import Company, Profile


@dataclass(frozen=True)
class Settings:
    webhook_url: str | None
    llm_api_key: str | None
    llm_model: str          # "" => provider default
    llm_provider: str       # "gemini" | "claude"
    role_id: str | None
    seen_path: str
    dry_run: bool


@dataclass(frozen=True)
class Config:
    profile: Profile
    companies: list[Company]
    settings: Settings


def load_profile(path: str) -> Profile:
    with open(path) as f:
        d = yaml.safe_load(f)
    low = lambda xs: [str(s).lower() for s in (xs or [])]
    return Profile(
        summary=d["summary"],
        title_include=low(d.get("title_include")),
        title_exclude=low(d.get("title_exclude")),
        locations_allow=low(d.get("locations_allow")),
        locations_block=low(d.get("locations_block")),
        freshness_days=int(d.get("freshness_days", 21)),
        ping_threshold=int(d.get("ping_threshold", 65)),
        digest_threshold=int(d.get("digest_threshold", 50)),
        high_score=int(d.get("high_score", 80)),
        high_fresh_hours=int(d.get("high_fresh_hours", 2)),
    )


def load_companies(path: str) -> list[Company]:
    with open(path) as f:
        d = yaml.safe_load(f)
    out = []
    for c in d.get("companies", []):
        out.append(Company(
            slug=c["slug"], ats=c["ats"], tier=c.get("tier", "target"),
            wd_host=c.get("wd_host"), wd_site=c.get("wd_site"),
        ))
    return out


def _truthy(v: str) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL") or None
    key = os.environ.get("LLM_API_KEY") or None
    dry = _truthy(os.environ.get("DRY_RUN", "")) or not webhook
    return Settings(
        webhook_url=webhook,
        llm_api_key=key,
        llm_model=os.environ.get("LLM_MODEL", ""),
        llm_provider=os.environ.get("LLM_PROVIDER", "gemini").lower(),
        role_id=os.environ.get("DISCORD_ROLE_ID") or None,
        seen_path=os.environ.get("SEEN_PATH", ".state/seen.json"),
        dry_run=dry,
    )


def load_config(profile_path: str = "config/profile.yaml",
                companies_path: str = "config/companies.yaml") -> Config:
    return Config(load_profile(profile_path), load_companies(companies_path), load_settings())
