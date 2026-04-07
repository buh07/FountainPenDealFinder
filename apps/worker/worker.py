import argparse
from datetime import datetime


def run_once() -> None:
    # Placeholder pipeline order aligned with project design.
    steps = [
        "collect listings",
        "normalize records",
        "classify listing and condition",
        "predict resale and auction outcomes",
        "compute proxy landed cost",
        "score and bucket deals",
        "generate daily markdown report",
    ]
    print(f"[{datetime.utcnow().isoformat()}] worker start")
    for idx, step in enumerate(steps, start=1):
        print(f"{idx}. {step}")
    print(f"[{datetime.utcnow().isoformat()}] worker done")


def main() -> None:
    parser = argparse.ArgumentParser(description="FountainPenDealFinder worker")
    parser.add_argument("--once", action="store_true", help="Run one pipeline pass")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        print("Use --once for now. Scheduler wiring is pending.")


if __name__ == "__main__":
    main()
