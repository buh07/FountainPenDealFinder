# FountainPenDealFinder

Personal deal-finding system for Japanese fountain-pen marketplaces.

## Monorepo Layout

- `apps/api`: FastAPI internal API
- `apps/worker`: scheduled collection and scoring worker
- `apps/dashboard`: lightweight review UI
- `apps/mcp-browser`: API-backed MCP-style browser wrapper (JavaScript)
- `apps/mcp-pricing`: API-backed MCP-style pricing wrapper (JavaScript)
- `packages/*`: shared modules and domain contracts
- `data/*`: fixtures, taxonomy, labels, generated reports
- `models/*`: model artifact placeholders
- `infra/*`: local infrastructure files
- `docs/*`: architecture and setup documentation

## Current API Endpoints

- `GET /health`
- `GET /health/metrics`
- `POST /health/alerts/dispatch`
- `POST /collect/run`
- `GET /listings`
- `GET /listings/{listing_id}`
- `POST /collect/refresh-ending`
- `POST /score/{listing_id}`
- `POST /predict/resale/{listing_id}`
- `POST /predict/auction/{listing_id}`
- `GET /proxy/listing/{listing_id}`
- `GET /proxy/top`
- `POST /review/{listing_id}`
- `POST /retrain/jobs`
- `GET /reports/daily/{date}`

## Source Ingestion Status

- Yahoo! JAPAN Auctions: connected via `YahooAuctionsAdapter` in `apps/api/app/adapters/yahoo_auctions.py`
- Yahoo! Fleamarket: connected via `YahooFleaMarketAdapter` in `apps/api/app/adapters/yahoo_flea_market.py`
- Mercari: connected via `MercariAdapter` in `apps/api/app/adapters/mercari.py`
- Rakuma: connected via `RakumaAdapter` in `apps/api/app/adapters/rakuma.py`
- Fallback source: fixture data in `data/fixtures/listings_sample.json` is applied per-source when connector collection fails or returns empty.

## Pricing vs Proxy Tracking

- Pricing models are isolated in `apps/api/app/services/pricing_models.py` (resale + auction prediction).
- Proxy economics and ranking are isolated in `apps/api/app/services/proxy_tracker.py`.
- Proxy pricing/coupon logic is data-backed through `proxy_pricing_policy` and `coupon_rule` tables.
- Score computation consumes proxy outputs (`expected_profit_jpy`, `expected_profit_pct`) rather than duplicating proxy math inline.

## Worker Modes

- One full run:

```bash
python -m apps.worker.worker --once
```

- One ending-auction refresh run:

```bash
python -m apps.worker.worker --ending-refresh-once --ending-window-hours 24
```

- Recurring scheduler loop:

```bash
python -m apps.worker.worker --daemon
```

Worker cadence defaults are configurable via `.env`:

- `WORKER_FIXED_SOURCE_INTERVAL_SECONDS`
- `WORKER_ENDING_AUCTIONS_INTERVAL_SECONDS`
- `WORKER_IDLE_SLEEP_SECONDS`
- `WORKER_ENDING_AUCTION_WINDOW_HOURS`
- `WORKER_DISPATCH_HEALTH_ALERTS`
- `WORKER_HEALTH_ALERT_WINDOW_HOURS`

## Quick Start

1. Start local infra (Postgres + Redis + API):

```bash
make up
```

1. Create and activate Python environment, then install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r apps/api/requirements.txt -r apps/worker/requirements.txt
```

1. Run migrations:

```bash
make db-upgrade
```

1. Run API:

```bash
make api
```

1. Trigger one collection/scoring run:

```bash
curl -X POST http://localhost:8000/collect/run
```

1. Trigger ending-auctions-only refresh:

```bash
curl -X POST 'http://localhost:8000/collect/refresh-ending?window_hours=24'
```

1. Open dashboard review UI:

```bash
python -m http.server 8080 -d apps/dashboard/public
```

1. Build historical datasets + train baseline artifacts:

```bash
python scripts/build_historical_datasets.py
python scripts/train_baseline_models.py
python scripts/evaluate_baseline_models.py --report-path models/eval/baseline_eval_v1.json
```

1. Run the same build/train/evaluate pipeline through API:

```bash
curl -X POST http://localhost:8000/retrain/jobs
```

## Baseline Model Gate

- Retrain jobs now run three steps: dataset build, training, and evaluation gate.
- Evaluation report is written to `BASELINE_EVAL_REPORT_PATH` (default: `models/eval/baseline_eval_v1.json`).
- Retrain endpoint returns `status=error` if evaluation gates fail.

Gate-related `.env` settings:

- `BASELINE_EVAL_REPORT_PATH`
- `BASELINE_EVAL_MIN_ROWS`
- `BASELINE_EVAL_RESALE_MAX_MAPE`
- `BASELINE_EVAL_AUCTION_MAX_MAPE`

## Monitoring and Tests

- Health metrics and alert signals:

```bash
curl 'http://localhost:8000/health/metrics?window_hours=24'
```

- Dispatch current health alerts to configured webhook destination:

```bash
curl -X POST 'http://localhost:8000/health/alerts/dispatch?window_hours=24'
```

- Alert/webhook settings:
- `MONITORING_ALERT_WEBHOOK_URL`
- `MONITORING_ALERT_WEBHOOK_TIMEOUT_SECONDS`

- Parser regression and monitoring tests:

```bash
python -m pytest apps/api/tests -q
```

## Migration Commands

- Upgrade to latest migration:

```bash
make db-upgrade
```

- Create new migration after model changes:

```bash
make db-revision m="add_new_table"
```

- Roll back one migration:

```bash
make db-downgrade
```

## Notes

- Default setup is Postgres-first and migration-driven via Alembic.
- For development continuity, fixture fallback can be toggled with `USE_FIXTURE_FALLBACK`.
