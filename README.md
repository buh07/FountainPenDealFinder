# FountainPenDealFinder

Personal deal-finding system for Japanese fountain-pen marketplaces.

Operational guide: see `RUN.md`.

## Monorepo Layout

- `apps/api`: FastAPI internal API
- `apps/worker`: scheduled collection and scoring worker
- `apps/dashboard`: lightweight review UI
- `apps/mcp-browser`: API-backed MCP SDK browser server over stdio (JavaScript)
- `apps/mcp-pricing`: API-backed MCP SDK pricing server over stdio (JavaScript)
- `apps/mcp-proxy`: proxy/coupon MCP SDK server over stdio (JavaScript)
- `apps/mcp-classification`: classification/taxonomy/review MCP SDK server over stdio (JavaScript)
- `apps/mcp-deal-scoring`: deal-ranking/report MCP SDK server over stdio (JavaScript)
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
- `GET /listings?limit=<n>&offset=<n>`
- `GET /listings?sort_by=<risk_adjusted|flat_profit|percent_profit>`
- `GET /listings?listing_type=auction&ending_within_hours=<n>`
- `GET /listings?since_hours=<n>`
- `GET /listings/{listing_id}`
- `GET /listings/{listing_id}/images`
- `POST /collect/refresh-ending`
- `POST /collect/refresh-priority`
- `POST /score/{listing_id}`
- `POST /predict/resale/{listing_id}`
- `POST /predict/auction/{listing_id}`
- `GET /proxy/listing/{listing_id}`
- `GET /proxy/top`
- `POST /review/{listing_id}`
- `POST /retrain/jobs`
- `GET /retrain/models/{task}/active`
- `GET /retrain/models/{task}/versions`
- `POST /retrain/models/{task}/rollback`
- `GET /reports/daily/{date}`
- `GET /taxonomy/standard`

## Taxonomy and Condition Standard

- A canonical taxonomy standard now unifies ingestion labels and resale training labels:
  - category (for example `japanese_core`, `japanese_premium`, `european_luxury`, `other`)
  - canonical `brand` and `line`
  - canonical `classification_id` (`brand_line` normalized)
- Taxonomy seed depth was expanded for target brands (Pilot/Namiki, Sailor, Platinum, Nakaya, Pelikan, Montblanc) with major line aliases.
- Canonical condition grades are constrained to:
  - `A`, `B+`, `B`, `C`, `Parts/Repair`
- Condition taxonomy endpoint now also exposes:
  - `condition_taxonomy` grade definitions
  - `damage_flag_taxonomy` (including deep scratches, cap/clip/thread damage, nib/feed uncertainty, urushi/maki-e wear, missing converter/box)
- The standard is exposed at:
  - `GET /taxonomy/standard`
- Source rows and feedback rows are normalized to this standard before model training.

## Feedback-Driven Autolabel Loop

- Review payloads now support corrected brand/line/condition and pricing labels:
  - `corrected_brand`, `corrected_line`, `corrected_condition_grade`
  - `corrected_ask_price_jpy`, `corrected_sold_price_jpy`, `corrected_item_count`
  - `taxonomy_aliases` (for adding aliases to new pen types)
- Review corrections are persisted as training examples and can append:
  - taxonomy type aliases to `TAXONOMY_FEEDBACK_TYPES_PATH`
  - pricing feedback rows to `FEEDBACK_PRICING_LABELS_PATH`
- Retrain pipeline (`build_historical_datasets.py` -> `train_baseline_models.py`) consumes these normalized feedback rows.

## Source Ingestion Status

- Yahoo! JAPAN Auctions: connected via `YahooAuctionsAdapter` in `apps/api/app/adapters/yahoo_auctions.py`
- Yahoo! Fleamarket: connected via `YahooFleaMarketAdapter` in `apps/api/app/adapters/yahoo_flea_market.py`
- Mercari: connected via `MercariAdapter` in `apps/api/app/adapters/mercari.py`
- Rakuma: connected via `RakumaAdapter` in `apps/api/app/adapters/rakuma.py`
- Fallback source: fixture data in `data/fixtures/listings_sample.json` is applied per-source when connector collection fails or returns empty.
- Stale fixture behavior: when filtered fixture rows are empty for a source, latest per-source fixture rows are returned with `raw_attributes.fixture_stale_fallback=true`.

## Price Quality Gating

- Every listing now resolves to `price_status` in API summaries:
  - `valid`: any positive `current_price_jpy` or `price_buy_now_jpy`
  - `missing`: no positive price and no parser-failure marker
  - `parse_error`: no positive price and `raw_attributes.price_parse_error=true`
- `missing` listings are forced to `discard` with neutralized profit outputs.
- `parse_error` listings trigger one repair attempt (text fallback + detail fetch). If unresolved, they remain `potential` with neutralized profit and review flags.
- API listing responses include `risk_flags` to expose these data-quality states.

## Multi-Stage Classification

- Classification now runs a 6-stage flow:
  - Stage 1 text candidate extraction
  - Stage 2 optional image-assisted disambiguation (`IMAGE_CLASSIFIER_ENABLED`)
  - Stage 3 lot decomposition
  - Stage 4 taxonomy resolution
  - Stage 5 canonical condition normalization (`A`, `B+`, `B`, `C`, `Parts/Repair`)
  - Stage 6 uncertainty tags + explanation payload
- Stage 5 condition extraction now includes expanded defect/completeness flags (for example `deep_scratches`, `cap_band_damage`, `clip_damage`, `thread_damage`, `nib_tipping_unclear`, `feed_issue_possible`, `missing_converter`, `missing_box`).
- If image stage is disabled or image evidence is unavailable, classification falls back to text-only behavior.

## Model Version Lifecycle

- Retrain now publishes immutable timestamped artifacts per task (`resale`, `auction`) under `MODEL_VERSION_ROOT`.
- Inference resolves artifacts through active pointer files:
  - `MODEL_ACTIVE_POINTER_RESALE`
  - `MODEL_ACTIVE_POINTER_AUCTION`
- Pointer switching happens only after successful retrain/evaluation gate.
- Rollback is supported through:
  - `POST /retrain/models/{task}/rollback`

## Report Window Rules

- Daily reports use timezone-aware filtering:
  - Fixed-price listings: report-date day window in `DEFAULT_TIMEZONE`.
  - Auctions: rolling `[generated_at, generated_at+24h)` and only when `ends_at` is known.
- Report/listing ranking views now support:
  - `risk_adjusted` (default)
  - `flat_profit`
  - `percent_profit`
- `GET /reports/daily/{date}` accepts `sort_by=<risk_adjusted|flat_profit|percent_profit>`.

## Pricing vs Proxy Tracking

- Pricing models are isolated in `apps/api/app/services/pricing_models.py` (resale + auction prediction).
- Proxy economics and ranking are isolated in `apps/api/app/services/proxy_tracker.py`.
- Proxy pricing/coupon logic is data-backed through `proxy_pricing_policy` and `coupon_rule` tables.
- Score computation consumes proxy outputs (`expected_profit_jpy`, `expected_profit_pct`) rather than duplicating proxy math inline.
- Proxy output now includes marketplace compatibility checks, first-time-user friction penalties, and a separate risk-adjusted-cost recommendation (`best_proxy_by_risk_adjusted_cost`).

## Worker Modes

- One full run:

```bash
python3 -m apps.worker.worker --once
```

- One ending-auction refresh run:

```bash
python3 -m apps.worker.worker --ending-refresh-once --ending-window-hours 24
```

- One priority refresh run:

```bash
python3 -m apps.worker.worker --priority-refresh-once --priority-window-hours 2 --priority-score-threshold 0.55
```

- Recurring scheduler loop:

```bash
python3 -m apps.worker.worker --daemon
```

Worker cadence defaults are configurable via `.env`:

- `WORKER_FIXED_SOURCE_INTERVAL_SECONDS`
- `WORKER_ENDING_AUCTIONS_INTERVAL_SECONDS`
- `WORKER_PRIORITY_INTERVAL_SECONDS`
- `WORKER_IDLE_SLEEP_SECONDS`
- `WORKER_ENDING_AUCTION_WINDOW_HOURS`
- `WORKER_PRIORITY_WINDOW_HOURS`
- `PRIORITY_SCORE_THRESHOLD`
- `WORKER_DISPATCH_HEALTH_ALERTS`
- `WORKER_HEALTH_ALERT_WINDOW_HOURS`

Priority candidate ranking now blends five factors: underpricing signal, confidence, urgency, estimated absolute value, and class rarity.

## Object Storage Capture

- Local object storage captures listing assets with metadata persisted in `listing_asset`.
- Captured asset types:
  - `page_capture` (HTML/text payload capture)
  - `image` (downloaded listing images)
- Capture behavior is policy-driven and disabled by default:
  - `OBJECT_STORE_ENABLE_CAPTURE`
  - `OBJECT_STORE_CAPTURE_POLICY` (`none`, `all`, `scored_only`, `ending_soon_only`, `scored_or_ending_soon`)
  - `OBJECT_STORE_ROOT`

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

1. Trigger high-priority ending-auctions refresh:

```bash
curl -X POST 'http://localhost:8000/collect/refresh-priority?window_hours=2&threshold=0.55'
```

1. Open dashboard review UI:

```bash
python3 -m http.server 8080 -d apps/dashboard/public
```

Dashboard updates:
- ranking-view toggle (`risk_adjusted`, `flat_profit`, `percent_profit`)
- bucket filter
- thumbnail gallery per listing
- confidence component breakdown
- local "watch this auction" action (browser-local watchlist)

1. Build historical datasets + train baseline artifacts:

```bash
python3 scripts/build_historical_datasets.py
python3 scripts/train_baseline_models.py
python3 scripts/evaluate_baseline_models.py --report-path models/eval/baseline_eval_v1.json
```

1. Run the same build/train/evaluate pipeline through API:

```bash
curl -X POST http://localhost:8000/retrain/jobs
```

## Baseline Model Gate

- Retrain jobs now run three steps: dataset build, training, and evaluation gate.
- Training/evaluation now use a deterministic hash split (default `train_ratio=0.8`), and gate metrics are computed on holdout rows.
- Evaluation report is written to `BASELINE_EVAL_REPORT_PATH` (default: `models/eval/baseline_eval_v1.json`).
- Evaluation report includes `MAPE`, `WAPE`, and `P95_APE` summaries.
- Holdout policy can be enforced (`BASELINE_EVAL_REQUIRE_HOLDOUT=true`) so training-data fallback metrics are informational only.
- Candidate-vs-active promotion now supports bootstrap significance gating on holdout errors.
- Retrain endpoint returns `status=error` if evaluation gates fail.
- On success, retrain publishes versioned artifacts and flips active pointers.

Gate-related `.env` settings:

- `BASELINE_EVAL_REPORT_PATH`
- `BASELINE_EVAL_MIN_ROWS`
- `BASELINE_EVAL_RESALE_MAX_MAPE`
- `BASELINE_EVAL_AUCTION_MAX_MAPE`
- `BASELINE_EVAL_REQUIRE_HOLDOUT`
- `BASELINE_EVAL_BOOTSTRAP_SAMPLES`
- `BASELINE_EVAL_SIGNIFICANCE_ALPHA`
- `MODEL_VERSION_ROOT`
- `MODEL_ACTIVE_POINTER_RESALE`
- `MODEL_ACTIVE_POINTER_AUCTION`

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
- `MONITORING_ALERT_DEDUPE_WINDOW_SECONDS`
- `MONITORING_ALERT_RETRY_ATTEMPTS`
- `MONITORING_ALERT_RETRY_BACKOFF_SECONDS`
- `MONITORING_MAX_MODEL_AGE_HOURS`
- `MONITORING_MAX_LISTING_STALENESS_HOURS`
- `TAXONOMY_SEED_PATH`
- `TAXONOMY_FEEDBACK_TYPES_PATH`
- `FEEDBACK_PRICING_LABELS_PATH`
- `CORS_ALLOW_ORIGINS`
- `CORS_ALLOW_METHODS`
- `CORS_ALLOW_HEADERS`
- `PROXY_COUPON_MAX_EXACT_STACKABLE`
- `PROXY_COUPON_FALLBACK_TOP_STACKABLE`
- `PROXY_FIRST_TIME_USER_PENALTY_JPY`
- `IMAGE_CLASSIFIER_BLEND_MIN_CONFIDENCE`
- `CLASSIFICATION_CALIBRATION_MIN_ROWS`
- `CLASSIFICATION_CALIBRATION_BIN_COUNT`
- `RESALE_BRAND_MIN_SAMPLES`

- Alert dispatch reliability:
- dispatch events are persisted to `health_alert_event` for audit/history
- duplicate alert signatures are rate-limited within cooldown and return `reason=deduped_recent_alert`
- transient webhook failures now retry with exponential backoff before failing
- dispatch responses include dedupe metadata (`deduped`, `cooldown_remaining_seconds`, `alert_signature`)
- health metrics also surface ingestion/retrain failure telemetry counters and latest failure reason fields
- health metrics also expose active model version IDs and model age (hours), with stale-model alerts
- health metrics include freshness signals (`latest_non_stale_listing_at`, `listing_freshness_hours`, and `listing_data_stale` alert)

- Parser regression and monitoring tests:

```bash
python3 -m pytest apps/api/tests -q
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
