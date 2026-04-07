import argparse
import time
from datetime import date, datetime

from apps.api.app.core.config import get_settings
from apps.api.app.db import SessionLocal, init_db
from apps.api.app.services.alerting import dispatch_health_alerts
from apps.api.app.services.monitoring import build_health_metrics
from apps.api.app.services.pipeline import run_collection_pipeline, run_ending_auction_refresh


def run_once(report_date: date | None) -> None:
    settings = get_settings()
    print(f"[{datetime.utcnow().isoformat()}] worker start")
    init_db()
    with SessionLocal() as db:
        result = run_collection_pipeline(db, report_date)
        if settings.worker_dispatch_health_alerts:
            metrics = build_health_metrics(db, window_hours=settings.worker_health_alert_window_hours)
            dispatch_result = dispatch_health_alerts(metrics)
            print(
                "health alert dispatch: "
                f"sent={dispatch_result.sent} "
                f"reason={dispatch_result.reason} "
                f"alert_count={dispatch_result.alert_count}"
            )
    print(
        "completed run: "
        f"ingested={result['ingested_count']} "
        f"scored={result['scored_count']} "
        f"confident={result['confident_count']} "
        f"potential={result['potential_count']}"
    )
    print(f"[{datetime.utcnow().isoformat()}] worker done")


def run_ending_refresh_once(window_hours: int) -> None:
    settings = get_settings()
    print(f"[{datetime.utcnow().isoformat()}] ending-refresh start")
    init_db()
    with SessionLocal() as db:
        result = run_ending_auction_refresh(db, window_hours=window_hours)
        if settings.worker_dispatch_health_alerts:
            metrics = build_health_metrics(db, window_hours=settings.worker_health_alert_window_hours)
            dispatch_result = dispatch_health_alerts(metrics)
            print(
                "health alert dispatch: "
                f"sent={dispatch_result.sent} "
                f"reason={dispatch_result.reason} "
                f"alert_count={dispatch_result.alert_count}"
            )
    print(
        "completed ending refresh: "
        f"window={result['window_hours']}h "
        f"ingested={result['ingested_count']} "
        f"scored={result['scored_count']}"
    )
    print(f"[{datetime.utcnow().isoformat()}] ending-refresh done")


def run_scheduler_loop(
    fixed_interval_seconds: int,
    ending_interval_seconds: int,
    idle_sleep_seconds: int,
    ending_window_hours: int,
) -> None:
    fixed_interval = max(60, fixed_interval_seconds)
    ending_interval = max(60, ending_interval_seconds)
    idle_sleep = max(1, idle_sleep_seconds)

    next_fixed = 0.0
    next_ending = 0.0

    print(
        "scheduler started: "
        f"fixed_every={fixed_interval}s "
        f"ending_every={ending_interval}s "
        f"window={ending_window_hours}h"
    )

    while True:
        now = time.time()

        if now >= next_fixed:
            run_once(report_date=None)
            next_fixed = now + fixed_interval

        if now >= next_ending:
            run_ending_refresh_once(window_hours=ending_window_hours)
            next_ending = now + ending_interval

        time.sleep(idle_sleep)


def main() -> None:
    settings = get_settings()

    parser = argparse.ArgumentParser(description="FountainPenDealFinder worker")
    parser.add_argument("--once", action="store_true", help="Run one pipeline pass")
    parser.add_argument(
        "--ending-refresh-once",
        action="store_true",
        help="Run one ending-auctions-only refresh pass",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run recurring scheduler loop",
    )
    parser.add_argument(
        "--report-date",
        type=str,
        default=None,
        help="Optional report date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--fixed-interval-seconds",
        type=int,
        default=settings.worker_fixed_source_interval_seconds,
        help="Recurring collection interval for full-source runs",
    )
    parser.add_argument(
        "--ending-interval-seconds",
        type=int,
        default=settings.worker_ending_auctions_interval_seconds,
        help="Recurring refresh interval for ending auctions",
    )
    parser.add_argument(
        "--idle-sleep-seconds",
        type=int,
        default=settings.worker_idle_sleep_seconds,
        help="Worker scheduler idle sleep between checks",
    )
    parser.add_argument(
        "--ending-window-hours",
        type=int,
        default=settings.worker_ending_auction_window_hours,
        help="Ending-auction refresh horizon in hours",
    )
    args = parser.parse_args()

    if args.once:
        parsed_date = None
        if args.report_date:
            parsed_date = date.fromisoformat(args.report_date)
        run_once(parsed_date)
        return

    if args.ending_refresh_once:
        run_ending_refresh_once(window_hours=max(1, args.ending_window_hours))
        return

    if args.daemon or settings.worker_enable_scheduler:
        try:
            run_scheduler_loop(
                fixed_interval_seconds=args.fixed_interval_seconds,
                ending_interval_seconds=args.ending_interval_seconds,
                idle_sleep_seconds=args.idle_sleep_seconds,
                ending_window_hours=max(1, args.ending_window_hours),
            )
        except KeyboardInterrupt:
            print("scheduler stopped")
        return

    print("No mode selected. Use --once, --ending-refresh-once, or --daemon.")


if __name__ == "__main__":
    main()
