"""Bulk-import company slugs into config/companies.yaml.

Run: python -m job_radar.seed <list.csv> [config/companies.yaml]

Each line: ``slug,ats[,tier]`` (comma or tab separated). Lines starting with
``#`` are ignored. Entries are deduped against what's already in the YAML, so
this is safe to run repeatedly against community-maintained slug dumps.
"""
import sys
from pathlib import Path

import yaml

VALID_ATS = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters"}


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in line.replace("\t", ",").split(",") if p.strip()]
    if len(parts) < 2:
        return None
    slug, ats = parts[0], parts[1].lower()
    if ats not in VALID_ATS:
        return None
    tier = parts[2].lower() if len(parts) > 2 else "target"
    return {"slug": slug, "ats": ats, "tier": tier}


def merge(existing: list[dict], lines) -> tuple[list[dict], int]:
    seen = {(c["slug"], c["ats"]) for c in existing}
    out = list(existing)
    added = 0
    for line in lines:
        rec = parse_line(line)
        if rec is None:
            continue
        key = (rec["slug"], rec["ats"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
        added += 1
    return out, added


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m job_radar.seed <list.csv> [config/companies.yaml]")
        return 1
    src = Path(argv[0])
    cfg = Path(argv[1]) if len(argv) > 1 else Path("config/companies.yaml")
    data = (yaml.safe_load(cfg.read_text()) if cfg.exists() else None) or {"companies": []}
    companies, added = merge(data.get("companies", []), src.read_text().splitlines())
    data["companies"] = companies
    cfg.write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"added {added}; total {len(companies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
