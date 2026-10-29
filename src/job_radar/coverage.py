"""How much of what the public lists show does the tracker actually hold?

The Simplify feeds and the Canadian list are fetched every run anyway, so they double as
an answer key: every open role in them that passes the profile rules and has a term he
can take should be either in the Sheet or scored and judged a poor fit. Anything else is
a role the radar never looked at, which is the number to drive to zero.
"""
from . import rolekey, topn
from .filters import passes_rules


def report(postings, sheet_rows, cache, profile, now) -> dict:
    sheet_keys = {rolekey.key(r.get("Company", ""), r.get("Role", "")) for r in sheet_rows}
    seen_keys: dict = {}
    for p in postings:
        if p.ats not in rolekey.AGGREGATORS:
            continue
        if not passes_rules(p, profile, now):
            continue
        k = rolekey.key(p.company, p.title)
        term_row = {"Term": p.title, "Status": "New", "Applied": "FALSE"}
        if not topn.eligible(term_row):
            continue
        state = ("in_sheet" if k in sheet_keys else
                 "scored_low" if p.uid in cache else "never_scored")
        # a role listed by both feeds counts once, at its best state
        order = {"in_sheet": 0, "scored_low": 1, "never_scored": 2}
        if k not in seen_keys or order[state] < order[seen_keys[k][0]]:
            seen_keys[k] = (state, p)
    out = {"in_sheet": 0, "scored_low": 0, "never_scored": 0}
    missing = []
    for state, p in seen_keys.values():
        out[state] += 1
        if state == "never_scored":
            missing.append(p.title)
    total = sum(out.values()) or 1
    out["coverage_pct"] = round(100 * (out["in_sheet"] + out["scored_low"]) / total, 1)
    out["missing_examples"] = sorted(missing)[:10]
    return out
