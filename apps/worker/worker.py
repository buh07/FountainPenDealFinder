import argparse
from datetime import date, datetime

from apps.api.app.db import SessionLocal, init_db
from apps.api.app.services.pipeline import run_collection_pipeline


def run_once(report_date: date | None) -> None:
    print(f"[{datetime.utcnow().isoformat()}] worker start")
    init_db()
    with SessionLocal() as db:
        result = run_collection_pipeline(db, report_date)
    print(
        "completed run: "
        f"ingested={result['ingested_count']} "
        f"scored={result['scored_count']} "
        f"confident={result['confident_count']} "
        f"potential={result['potential_count']}"
    )
    print(f"[{datetime.utcnow().isoformat()}] worker done")


def main() -> None:
    parser = argparse.ArgumentParser(description="FountainPenDealFinder worker")
    parser.add_argument("--once", action="store_true", help="Run one pipeline pass")
    parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Optional report date in YYYY-MM-DD format",
    )
    args = parser.parse_args()

    if args.once:
        parsed_date = None
        if args.report_date:
            parsed_date = date.fromisoformat(args.report_date)
        run_once(parsed_date)
    else:
        print("Use --once for now. Scheduler wiring is pending.")


if __name__ == "__main__":
    main()
