"""The daily "Top 30" list: which open roles are most worth applying to right now.

Pure logic over tracker rows (same dicts as tracker.py), so it is fully unit-testable.
The rank starts from the LLM fit score and then applies Taka's own priorities, agreed
Oct 21 2026:
  1. applied ML / LLM engineering first (the Summer 2027 internship lane),
  2. software roles with AI work next (the 12-month co-op lane), and 12 to 16 month
     terms get a boost because his co-op must be one continuous 12 or 16 month term,
  3. low-level roles (compilers, kernels, embedded, hardware) rank low, he doesn't
     want that work, and research-titled roles rank lower too,
  4. Canada and remote beat US on-site.
Anything he flagged in the Priority column, or with a deadline inside two weeks, jumps up.
"""
import re
from datetime import date

from . import tracker

TOP_N = 30

# Keyword sets matched against the role title (lower-cased, padded with spaces so
# short tokens like " ml " only match whole words).
_ML = (" ml ", "machine learning", " ai ", " ai/", "/ai ", "artificial intelligence", "llm",
       "genai", "generative", "applied ai", "applied science", "nlp", "data scien",
       "deep learning", "mlops", "computer vision", "agentic")
_LOW_LEVEL = ("compiler", "kernel", "embedded", "firmware", "fpga", "asic", "rtl",
              "verification", "silicon", "hardware", "driver", "gpu", "c++", "dsp",
              "performance engineer", "systems-on-chip", "soc ", "physical design")
_RESEARCH = ("research", "researcher")
_LONG_TERM = re.compile(r"\b(12|16)[\s-]*(months?|mos?)\b|\b(12|16)m\b")
# Starts he can't take (classes through April 2027). tracker.wrong_term already catches
# "Winter/Spring 2027" and older years; this adds month-named starts like "Jan 2027".
_EARLY_2027 = re.compile(r"\b(jan|feb|mar|apr)[a-z]*\.?\s*(-\s*\w+\s*)?2027")
_US_REMOTE = ("remote in us", "us - remote", "remote (us", "remote, us", "united states",
              "usa")
_RESTRICTED = ("students only", "limited to", "only open to")
_CANADA = ("canada", ", on", ", bc", ", qc", ", ab", "toronto", "mississauga", "markham",
           "waterloo", "ottawa", "montreal", "vancouver", "calgary", "remote")

# Pseudo-companies whose real company name lives in the Role ("Pebl: SWE Intern").
_PSEUDO = {"simplify", "canadalist"}


def display(row) -> tuple[str, str]:
    """(company, role) for humans: unpacks 'Company: Role' for the aggregator feeds."""
    company = (row.get("Company") or "").strip()
    role = (row.get("Role") or "").strip()
    if company.lower() in _PSEUDO and ": " in role:
        company, role = role.split(": ", 1)
    return company, role


def _has(text: str, words) -> bool:
    return any(w in text for w in words)


def score(row, today: date) -> tuple[int, list[str]]:
    """Priority score (higher = apply sooner) and the short reasons behind it."""
    _, role = display(row)
    t = f" {role.lower()} "
    term = (row.get("Term") or "").lower()
    loc = (row.get("Location") or "").lower()
    s = tracker.fit(row)
    why = [f"fit {s}"]

    low_level = _has(t, _LOW_LEVEL)
    if low_level:
        s -= 25
        why.append("low-level")
    elif _has(t, _ML) or (row.get("Resume") or "").strip().upper() == "AI":
        s += 10
        why.append("ML/AI")

    if _has(t, _RESEARCH) and "data scien" not in t:
        s -= 15
        why.append("research")

    if _LONG_TERM.search(term) or _LONG_TERM.search(t):
        s += 8
        why.append("12-16 mo")

    if _has((row.get("Why") or "").lower(), _RESTRICTED):
        s -= 20
        why.append("may be limited to some schools")

    if loc and _has(loc, _CANADA) and not _has(loc, _US_REMOTE):
        s += 5
        why.append("Canada/remote")
    elif loc:
        s -= 5

    rank = tracker.priority_rank(row)
    if rank == 0:
        s += 30
        why.append("flagged must")
    elif rank == 1:
        s += 15
        why.append("flagged high")

    d = tracker.parse_date(row.get("Deadline"))
    if d is not None and 0 <= (d - today).days <= 14:
        s += 10
        why.append(f"due {d.isoformat()}")

    p = tracker.parse_date(row.get("Posted"))
    if p is not None and 0 <= (today - p).days <= 7:
        s += 3
        why.append("new this week")
    return s, why


def eligible(row) -> bool:
    """Open, not applied or skipped, and not a work term he can't take."""
    if tracker.status(row) != tracker.PENDING or tracker.wrong_term(row):
        return False
    term = (row.get("Term") or "").lower()
    if "winter" in term and "2028" not in term:
        return False            # an undated "Winter co-op" posted now means Winter 2027
    return not _EARLY_2027.search(term)


def top(rows, today: date, n: int = TOP_N) -> list[tuple[int, list[str], dict]]:
    """The n best open roles, highest priority first. One entry per (company, role) so
    the same job posted in three cities takes one slot, not three."""
    ranked = []
    seen = set()
    scored = sorted(((score(r, today), r) for r in rows if eligible(r)),
                    key=lambda x: x[0][0], reverse=True)
    for (s, why), r in scored:
        key = tuple(x.lower() for x in display(r))
        if key in seen:
            continue
        seen.add(key)
        ranked.append((s, why, r))
        if len(ranked) >= n:
            break
    return ranked


TOP_HEADERS = ["Rank", "Priority score", "Company", "Role", "Term", "Location", "Deadline",
               "Resume", "Apply link", "Prepare", "Why it ranks here", "Fit reason"]


def _quote(s: str) -> str:
    return s.replace('"', '""')


def table(ranked, prepare_url: str | None = None) -> list[list]:
    """Rows for the Top tab. 'Prepare' is a link to wherever Taka asks Claude to prep the
    application (PREPARE_URL), with the exact words to paste; plain text if unset."""
    out = [TOP_HEADERS]
    for i, (s, why, r) in enumerate(ranked, start=1):
        company, role = display(r)
        ask = f"prep {company}: {role}"
        prep = (f'=HYPERLINK("{_quote(prepare_url)}", "{_quote(ask)}")' if prepare_url else ask)
        link = r.get("Link") or ""
        apply = f'=HYPERLINK("{_quote(link)}", "Apply")' if link else ""
        out.append([i, s, company, role, r.get("Term", ""), r.get("Location", ""),
                    r.get("Deadline", ""), r.get("Resume", ""), apply, prep,
                    ", ".join(why), r.get("Why", "")])
    return out
