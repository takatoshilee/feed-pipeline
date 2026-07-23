"""Google Careers (google.com/about/careers) - Google runs its own ATS, so none of
the standard adapters can see it; this was the blind spot that hid the Summer-2027
Software Developer Intern (Canada) posting until it was found by hand.

The results page is server-rendered HTML with job links of the form
  jobs/results/<numeric-id>-<hyphenated-title-slug>
which is stable and regex-extractable (verified live, July 2026). We poll the
Canada+intern query. Modelled as a single pseudo-company (slug 'google') like the
simplify feed, so the per-company silent-prime absorbs the existing backlog and only
genuinely NEW postings alert. Titles are rebuilt from the slug; no posted_at or
description is available at list time (title + location carry the rules filter and
the scorer copes with a missing description).
"""
import re

from ..models import Company, Posting

RESULTS = ("https://www.google.com/about/careers/applications/jobs/results"
           "?q=intern&location=Canada&page={page}")
JOB_URL = "https://www.google.com/about/careers/applications/jobs/results/{slug}"
PAGES = 2          # Canada+intern is currently a handful of rows; 2 pages is plenty
_SLUG = re.compile(r"jobs/results/(\d+)-([a-z0-9-]+)")
# Slug tokens that should render uppercase in the rebuilt title.
_UPPER = {"bs", "ms", "phd", "ai", "ml", "swe"}
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _title(slug_words: str) -> str:
    return " ".join(w.upper() if w in _UPPER else w.capitalize()
                    for w in slug_words.split("-"))


def parse(html_text: str) -> list[Posting]:
    out, seen = [], set()
    for jid, words in _SLUG.findall(html_text or ""):
        if jid in seen:
            continue                      # each job link appears several times per page
        seen.add(jid)
        out.append(Posting(
            uid=f"googlecareers:google:{jid}",
            ats="googlecareers",
            company="google",             # == slug; pseudo-company like 'simplify'
            title=f"Google: {_title(words)}",
            location="Canada",            # query-scoped; per-job cities need a job-page fetch
            url=JOB_URL.format(slug=f"{jid}-{words}"),
            posted_at=None,               # not exposed on the results page
            description="",
            raw={"id": jid, "slug": words},
        ))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    out, seen = [], set()
    for page in range(1, PAGES + 1):
        resp = await client.get(RESULTS.format(page=page), headers=_UA, timeout=30.0)
        if resp.status_code != 200:
            break
        batch = parse(resp.text)
        fresh = [p for p in batch if p.uid not in seen]
        if not fresh:
            break                         # page past the end repeats/empties
        seen.update(p.uid for p in fresh)
        out.extend(fresh)
    return out
