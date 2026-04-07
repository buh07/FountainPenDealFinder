# Setup

## Local development

1. Copy environment template:

```bash
cp .env.example .env
```

1. Start local infra (Postgres + Redis):

```bash
make up
```

1. Create and activate virtual environment, then install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r apps/api/requirements.txt -r apps/worker/requirements.txt
```

1. Apply migrations:

```bash
make db-upgrade
```

1. Start API:

```bash
make api
```

1. Run one full collection/scoring/report pass:

```bash
curl -X POST http://localhost:8000/collect/run
```

1. Run ending-auction refresh pass only:

```bash
curl -X POST 'http://localhost:8000/collect/refresh-ending?window_hours=24'
```

1. Check aggregated health metrics and alert signals:

```bash
curl 'http://localhost:8000/health/metrics?window_hours=24'
```

1. Dispatch current health alerts to webhook (if configured):

```bash
curl -X POST 'http://localhost:8000/health/alerts/dispatch?window_hours=24'
```

1. Start review dashboard:

```bash
make dashboard
```

## Worker modes

One-time full run:

```bash
python -m apps.worker.worker --once
```

One-time ending-auctions refresh:

```bash
python -m apps.worker.worker --ending-refresh-once --ending-window-hours 24
```

Recurring scheduler loop:

```bash
python -m apps.worker.worker --daemon
```

Scheduler environment knobs:

- `WORKER_ENABLE_SCHEDULER`
- `WORKER_FIXED_SOURCE_INTERVAL_SECONDS`
- `WORKER_ENDING_AUCTIONS_INTERVAL_SECONDS`
- `WORKER_IDLE_SLEEP_SECONDS`
- `WORKER_ENDING_AUCTION_WINDOW_HOURS`
- `WORKER_DISPATCH_HEALTH_ALERTS`
- `WORKER_HEALTH_ALERT_WINDOW_HOURS`

## Training pipeline

Build normalized historical datasets:

```bash
python scripts/build_historical_datasets.py
```

Train baseline artifacts:

```bash
python scripts/train_baseline_models.py
```

Evaluate baseline artifacts and write gate report:

```bash
python scripts/evaluate_baseline_models.py --report-path models/eval/baseline_eval_v1.json
```

Trigger the same flow through API:

```bash
curl -X POST http://localhost:8000/retrain/jobs
```

If evaluation gates fail, `/retrain/jobs` returns `status=error` and includes script logs in `details`.

## Manual review workflow

Submit review feedback via API:

```bash
curl -X POST http://localhost:8000/review/<listing_id> \
	-H 'Content-Type: application/json' \
	-d '{"action_type":"confirm_classification","notes":"looks correct"}'
```

This writes to both `manual_review` and `training_example` tables.

## Connector and reliability notes

- `INGESTION_RETRY_ATTEMPTS`, `INGESTION_RETRY_BACKOFF_SECONDS`, and parse-completeness settings control adapter reliability behavior.
- `BASELINE_EVAL_MIN_ROWS`, `BASELINE_EVAL_RESALE_MAX_MAPE`, and `BASELINE_EVAL_AUCTION_MAX_MAPE` control retrain quality gates.
- `MONITORING_MIN_SOURCE_COUNT`, `MONITORING_MIN_PARSE_COMPLETENESS`, `MONITORING_MIN_NON_DISCARD_RATE`, and `MONITORING_MAX_FALSE_POSITIVE_RATE` control `/health/metrics` alert thresholds.
- `MONITORING_ALERT_WEBHOOK_URL` and `MONITORING_ALERT_WEBHOOK_TIMEOUT_SECONDS` control webhook dispatch behavior for `/health/alerts/dispatch`.
- If local SSL trust chain is incomplete during development, connector-specific verify flags can be temporarily disabled.

## Test commands

Run parser regression and monitoring coverage:

```bash
python -m pytest apps/api/tests -q
```
