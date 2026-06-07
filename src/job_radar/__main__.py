import argparse
import asyncio
from dataclasses import replace

from .config import load_config
from .pipeline import backfill, preview, run


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="job_radar",
        description="Poll ATS job boards and ping Discord about relevant postings.",
    )
    ap.add_argument("--profile", default="config/profile.yaml", help="path to profile.yaml")
    ap.add_argument("--companies", default="config/companies.yaml", help="path to companies.yaml")
    ap.add_argument("--state", help="override the seen-set path")
    ap.add_argument("--dry-run", action="store_true",
                    help="print to console instead of posting to Discord")
    ap.add_argument("--prime", action="store_true",
                    help="mark everything seen without notifying (re-prime the radar)")
    ap.add_argument("--preview", action="store_true",
                    help="show what would surface from the current backlog, ranked; read-only")
    ap.add_argument("--backfill", action="store_true",
                    help="one-time: write current open matches to the Sheet (no pings, no state change)")
    ap.add_argument("--limit", type=int, help="only poll the first N companies (local testing)")
    ap.add_argument("--company", help="only poll this company slug (local testing)")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    config = load_config(args.profile, args.companies)

    settings = config.settings
    if args.state:
        settings = replace(settings, seen_path=args.state)
    if args.dry_run:
        settings = replace(settings, dry_run=True)

    companies = config.companies
    if args.company:
        companies = [c for c in companies if c.slug == args.company]
    if args.limit is not None:
        companies = companies[:args.limit]

    config = replace(config, companies=companies, settings=settings)
    if args.preview:
        asyncio.run(preview(config))
    elif args.backfill:
        asyncio.run(backfill(config))
    else:
        asyncio.run(run(config, force_prime=args.prime))


if __name__ == "__main__":
    main()
