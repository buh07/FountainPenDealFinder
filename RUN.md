# FountainPenDealFinder Runbook

This is the operator/developer guide for implementing, running, validating, and maintaining the project.

## 1. Environment Setup

1. Copy environment defaults:

```bash
cp .env.example .env
```

1. Start local services (Postgres + Redis):

```bash
make up
```

1. Create Python environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r apps/api/requirements.txt -r apps/worker/requirements.txt
```

1. Run DB migrations:

```bash
make db-upgrade
```

## 2. Start Services

1. Run API:

```bash
make api
```

1. Run dashboard (separate terminal):

```bash
python3 -m http.server 8080 -d apps/dashboard/public
```

1. Optional worker modes:

```bash
python3 -m apps.worker.worker --once
python3 -m apps.worker.worker --ending-refresh-once --ending-window-hours 24
python3 -m apps.worker.worker --priority-refresh-once --priority-window-hours 2 --priority-score-threshold 0.55
python3 -m apps.worker.worker --daemon
```

## 3. Daily Operations

### Run ingestion and scoring

```bash
curl -X POST http://localhost:8000/collect/run
```

### Run auction refresh jobs

```bash
curl -X POST 'http://localhost:8000/collect/refresh-ending?window_hours=24'
curl -X POST 'http://localhost:8000/collect/refresh-priority?window_hours=2&threshold=0.55'
```

### Query ranked listings and reports

```bash
curl 'http://localhost:8000/listings?limit=50&sort_by=risk_adjusted'
curl 'http://localhost:8000/listings?limit=50&sort_by=flat_profit'
curl 'http://localhost:8000/listings?limit=50&sort_by=percent_profit'
curl 'http://localhost:8000/reports/daily/2026-04-11?sort_by=risk_adjusted'
```

### Proxy diagnostics

```bash
curl 'http://localhost:8000/proxy/listing/<listing_id>'
curl 'http://localhost:8000/proxy/top?limit=50'
```

### Health and alert checks

```bash
curl 'http://localhost:8000/health/metrics?window_hours=24'
curl -X POST 'http://localhost:8000/health/alerts/dispatch?window_hours=24'
```

## 4. Manual Review and Feedback Loop

Submit review feedback:

```bash
curl -X POST http://localhost:8000/review/<listing_id> \
  -H 'Content-Type: application/json' \
  -d '{
    "action_type": "correct_classification",
    "corrected_brand": "Pilot",
    "corrected_line": "Custom 743",
    "corrected_condition_grade": "B+",
    "taxonomy_aliases": ["custom743","カスタム743"],
    "corrected_item_count": 1,
    "corrected_ask_price_jpy": 32000,
    "corrected_sold_price_jpy": 52000,
    "notes": "manual correction",
    "reviewer": "ops"
  }'
```

Feedback effects:
- `manual_review` + `training_example` DB rows are written.
- Type aliases append to `TAXONOMY_FEEDBACK_TYPES_PATH`.
- Pricing labels append to `FEEDBACK_PRICING_LABELS_PATH`.

## 5. Data Update Workflow

### Taxonomy updates

- Edit seed taxonomy: `data/taxonomy/taxonomy_v1_seed.csv`
- Add/adjust aliases through review feedback or direct JSONL edits:
  - `data/taxonomy/taxonomy_feedback_types.jsonl`

### Historical labeled data updates

- Add Pen_Swap raw rows:
  - `data/labeled/raw/pen_swap_sales.jsonl`
- Add Yahoo auction outcome rows:
  - `data/labeled/raw/yahoo_auction_outcomes.jsonl`
- Optional manual pricing feedback rows:
  - `data/labeled/raw/pen_swap_sales_feedback.jsonl`

### Rebuild normalized datasets

```bash
python3 scripts/build_historical_datasets.py
```

Outputs:
- `data/labeled/pen_swap_sales.csv`
- `data/labeled/yahoo_auction_outcomes.csv`

## 6. Retraining and Model Lifecycle

### CLI retrain flow

```bash
python3 scripts/train_baseline_models.py --resale-brand-min-samples 3
python3 scripts/evaluate_baseline_models.py --report-path models/eval/baseline_eval_v1.json
```

### API retrain flow (build + train + eval + promotion)

```bash
curl -X POST http://localhost:8000/retrain/jobs
```

### Model version inspection and rollback

```bash
curl 'http://localhost:8000/retrain/models/resale/active'
curl 'http://localhost:8000/retrain/models/resale/versions'
curl -X POST 'http://localhost:8000/retrain/models/resale/rollback' \
  -H 'Content-Type: application/json' \
  -d '{"version_id":"<version_id>"}'
```

Repeat with `auction` task as needed.

## 7. End-to-End Validation Commands

### A) Deterministic local validation (fixture-backed)

```bash
python3 -m compileall apps/api/app apps/worker scripts
python3.13 -m pytest -q
node --check apps/mcp-browser/src/index.js
node --check apps/mcp-pricing/src/index.js
node --check apps/mcp-proxy/src/index.js
node --check apps/mcp-classification/src/index.js
node --check apps/mcp-deal-scoring/src/index.js
```

Fixture-only pipeline smoke:

```bash
DATABASE_URL=sqlite:///./tmp_pipeline_local.db \
AUTO_CREATE_TABLES=true \
USE_FIXTURE_FALLBACK=true \
YAHOO_AUCTIONS_ENABLED=false \
YAHOO_FLEA_MARKET_ENABLED=false \
MERCARI_ENABLED=false \
RAKUMA_ENABLED=false \
BASELINE_EVAL_REQUIRE_HOLDOUT=false \
python3 - <<'PY'
from app.db import init_db, SessionLocal
from app.services.pipeline import run_collection_pipeline, run_ending_auction_refresh, run_priority_auction_refresh
from app.services.training_pipeline import run_baseline_training_pipeline
from app.services.monitoring import build_health_metrics

init_db()
with SessionLocal() as db:
    print("collect:", run_collection_pipeline(db))
with SessionLocal() as db:
    print("ending:", run_ending_auction_refresh(db, 24))
with SessionLocal() as db:
    print("priority:", run_priority_auction_refresh(db, window_hours=2, threshold=0.0))
status, details = run_baseline_training_pipeline()
print("retrain_status:", status)
print("retrain_details_head:", details.splitlines()[:8])
with SessionLocal() as db:
    metrics = build_health_metrics(db, 24)
    print("health_alerts:", metrics.alerts)
PY
```

### B) Live-source validation (best-effort)

```bash
DATABASE_URL=sqlite:///./tmp_pipeline_live.db \
AUTO_CREATE_TABLES=true \
USE_FIXTURE_FALLBACK=true \
YAHOO_AUCTIONS_ENABLED=true \
YAHOO_FLEA_MARKET_ENABLED=true \
MERCARI_ENABLED=true \
RAKUMA_ENABLED=true \
python3 - <<'PY'
from app.db import init_db, SessionLocal
from app.services.pipeline import run_collection_pipeline
from app.services.monitoring import build_health_metrics

init_db()
with SessionLocal() as db:
    result = run_collection_pipeline(db)
    print("collect:", result)
with SessionLocal() as db:
    metrics = build_health_metrics(db, 24)
    print("ingestion_failure_count:", metrics.ingestion_failure_count)
    print("latest_ingestion_failure_reason:", metrics.latest_ingestion_failure_reason)
    print("alerts:", metrics.alerts)
PY
```

Best-effort success definition:
- Pipeline run completes without crashing.
- Listings/report persistence succeeds.
- Source failures, if any, are surfaced through telemetry/alerts.

## 8. Key Configuration Knobs

- Ingestion reliability:
  - `INGESTION_RETRY_ATTEMPTS`
  - `INGESTION_RETRY_BACKOFF_SECONDS`
  - `INGESTION_PARSE_MIN_COMPLETENESS`
  - `INGESTION_PARSE_MIN_VALID_ROWS`
- Priority queue:
  - `WORKER_PRIORITY_WINDOW_HOURS`
  - `PRIORITY_SCORE_THRESHOLD`
  - `PRIORITY_VALUE_REFERENCE_JPY_CEILING`
- Proxy rules:
  - `PROXY_COUPON_MAX_EXACT_STACKABLE`
  - `PROXY_COUPON_FALLBACK_TOP_STACKABLE`
  - `PROXY_FIRST_TIME_USER_PENALTY_JPY`
- Retrain gates:
  - `BASELINE_EVAL_MIN_ROWS`
  - `BASELINE_EVAL_RESALE_MAX_MAPE`
  - `BASELINE_EVAL_AUCTION_MAX_MAPE`
  - `BASELINE_EVAL_REQUIRE_HOLDOUT`
  - `BASELINE_EVAL_BOOTSTRAP_SAMPLES`
  - `BASELINE_EVAL_SIGNIFICANCE_ALPHA`

## 9. Troubleshooting

- `collect/run` returns low counts:
  - Check source enable flags and `USE_FIXTURE_FALLBACK`.
  - Check `/health/metrics` source counts and ingestion failure reason fields.
- No ranked listings:
  - Verify `min_profit_jpy`, `min_profit_pct`, and confidence thresholds.
  - Inspect `price_status` and `risk_flags` on `/listings`.
- Retrain fails:
  - Check `models/eval/baseline_eval_v1.json` and `/retrain/jobs` `details`.
  - Relax holdout requirement temporarily only for local smoke validation.
- Proxy outputs look off:
  - Verify `proxy_pricing_policy` and `coupon_rule` rows.
  - Check `first_time_penalty_jpy` and `risk_adjusted_total_cost_jpy` fields.
