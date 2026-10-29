"""One key per real-world role, so the same job reached through several sources (the
company's own board, the Simplify feed, the Canadian list) counts once.

The key is (company, title) normalized: lower-cased, punctuation dropped, a trailing
season tag like "(Summer 2027)" removed, and the aggregator form "Company: Role" unpacked.
"""
import re

AGGREGATORS = {"simplify", "canadalist"}
_SEASON = re.compile(r"\((summer|fall|winter|spring)[^)]*\)", re.I)
_SUFFIX = ("inc", "corp", "ltd", "llc", "group", "technologies", "canada")


def _n(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def split(company: str, title: str) -> tuple[str, str]:
    """Real (company, role) for a row or posting, unpacking 'Pebl: SWE Intern'."""
    company, title = (company or "").strip(), (title or "").strip()
    if company.lower() in AGGREGATORS and ": " in title:
        company, title = title.split(": ", 1)
    return company, title


def company_key(company: str) -> str:
    n = _n(company)
    for suf in _SUFFIX:
        if n.endswith(suf) and len(n) > len(suf) + 2:
            n = n[: -len(suf)]
    return n


def key(company: str, title: str) -> tuple[str, str]:
    company, title = split(company, title)
    return company_key(company), _n(_SEASON.sub("", title))
