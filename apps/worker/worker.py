import argparse
import signal
import time
from datetime import date, datetime, timezone
from threading import Event

from apps.api.app.core.config import get_settings
from apps.api.app.db import SessionLocal, init_db
from apps.api.app.services.alerting import dispatch_health_alerts
from apps.api.app.services.monitoring import build_health_metrics
from apps.api.app.services.pipeline import (
    run_collection_pipeline,
    run_ending_auction_refresh,
    run_priority_auction_refresh,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_once(report_date: date | None) -> None:
    settings = get_settings()
    print(f"[{_utc_now().isoformat()}] worker start")
    init_db()
    with SessionLocal() as db:
        result = run_collection_pipeline(db, report_date)
        if settings.worker_dispatch_health_alerts:
            metrics = build_health_metrics(db, window_hours=settings.worker_health_alert_window_hours)
            dispatch_result = dispatch_health_alerts(db, metrics)
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
    print(f"[{_utc_now().isoformat()}] worker done")


def run_ending_refresh_once(window_hours: int) -> None:
    settings = get_settings()
    print(f"[{_utc_now().isoformat()}] ending-refresh start")
    init_db()
    with SessionLocal() as db:
        result = run_ending_auction_refresh(db, window_hours=window_hours)
        if settings.worker_dispatch_health_alerts:
            metrics = build_health_metrics(db, window_hours=settings.worker_health_alert_window_hours)
            dispatch_result = dispatch_health_alerts(db, metrics)
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
    print(f"[{_utc_now().isoformat()}] ending-refresh done")


def run_priority_refresh_once(window_hours: int, threshold: float) -> None:
    settings = get_settings()
    print(f"[{_utc_now().isoformat()}] priority-refresh start")
    init_db()
    with SessionLocal() as db:
        result = run_priority_auction_refresh(
            db,
            window_hours=window_hours,
            threshold=threshold,
        )
        if settings.worker_dispatch_health_alerts:
            metrics = build_health_metrics(db, window_hours=settings.worker_health_alert_window_hours)
            dispatch_result = dispatch_health_alerts(db, metrics)
            print(
                "health alert dispatch: "
                f"sent={dispatch_result.sent} "
                f"reason={dispatch_result.reason} "
                f"alert_count={dispatch_result.alert_count}"
            )
    print(
        "completed priority refresh: "
        f"window={result['window_hours']}h "
        f"threshold={result['threshold']:.2f} "
        f"candidates={result['candidate_count']} "
        f"ingested={result['ingested_count']} "
        f"scored={result['scored_count']}"
    )
    print(f"[{_utc_now().isoformat()}] priority-refresh done")


def run_scheduler_loop(
    fixed_interval_seconds: int,
    ending_interval_seconds: int,
    priority_interval_seconds: int,
    idle_sleep_seconds: int,
    ending_window_hours: int,
    priority_window_hours: int,
    priority_threshold: float,
    stop_event: Event,
) -> None:
    fixed_interval = max(60, fixed_interval_seconds)
    ending_interval = max(60, ending_interval_seconds)
    priority_interval = max(60, priority_interval_seconds)
    idle_sleep = max(1, idle_sleep_seconds)

    next_fixed = 0.0
    next_ending = 0.0
    next_priority = 0.0

    print(
        "scheduler started: "
        f"fixed_every={fixed_interval}s "
        f"ending_every={ending_interval}s "
        f"priority_every={priority_interval}s "
        f"window={ending_window_hours}h"
    )

    while not stop_event.is_set():
        now = time.time()
        due: list[tuple[float, str]] = []
        if now >= next_priority:
            due.append((next_priority, "priority"))
        if now >= next_ending:
            due.append((next_ending, "ending"))
        if now >= next_fixed:
            due.append((next_fixed, "fixed"))

        if due:
            # Run one due task per scheduler tick to prevent long sequential stacks.
            order = {"priority": 0, "ending": 1, "fixed": 2}
            due.sort(key=lambda item: (item[0], order.get(item[1], 99)))
            _scheduled_at, task_name = due[0]
            cycle_started = time.time()
            if task_name == "priority":
                run_priority_refresh_once(
                    window_hours=priority_window_hours,
                    threshold=priority_threshold,
                )
                next_priority = cycle_started + priority_interval
            elif task_name == "ending":
                run_ending_refresh_once(window_hours=ending_window_hours)
                next_ending = cycle_started + ending_interval
            else:
                run_once(report_date=None)
                next_fixed = cycle_started + fixed_interval
            continue

        stop_event.wait(idle_sleep)


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
        "--priority-refresh-once",
        action="store_true",
        help="Run one high-priority ending-auctions refresh pass",
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
        "--priority-interval-seconds",
        type=int,
        default=settings.worker_priority_interval_seconds,
        help="Recurring refresh interval for high-priority ending auctions",
    )
    parser.add_argument(
        "--ending-window-hours",
        type=int,
        default=settings.worker_ending_auction_window_hours,
        help="Ending-auction refresh horizon in hours",
    )
    parser.add_argument(
        "--priority-window-hours",
        type=int,
        default=settings.worker_priority_window_hours,
        help="Priority refresh horizon in hours",
    )
    parser.add_argument(
        "--priority-score-threshold",
        type=float,
        default=settings.priority_score_threshold,
        help="Priority refresh score threshold in [0,1]",
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

    if args.priority_refresh_once:
        run_priority_refresh_once(
            window_hours=max(1, args.priority_window_hours),
            threshold=max(0.0, min(1.0, args.priority_score_threshold)),
        )
        return

    if args.daemon or settings.worker_enable_scheduler:
        stop_event = Event()

        def _request_stop(signum, _frame):  # noqa: ANN001
            print(f"received signal {signum}; shutting down scheduler after current cycle")
            stop_event.set()

        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

        try:
            run_scheduler_loop(
                fixed_interval_seconds=args.fixed_interval_seconds,
                ending_interval_seconds=args.ending_interval_seconds,
                priority_interval_seconds=args.priority_interval_seconds,
                idle_sleep_seconds=args.idle_sleep_seconds,
                ending_window_hours=max(1, args.ending_window_hours),
                priority_window_hours=max(1, args.priority_window_hours),
                priority_threshold=max(0.0, min(1.0, args.priority_score_threshold)),
                stop_event=stop_event,
            )
        except KeyboardInterrupt:
            stop_event.set()
        print("scheduler stopped")
        return

    print("No mode selected. Use --once, --ending-refresh-once, --priority-refresh-once, or --daemon.")


if __name__ == "__main__":
    main()
