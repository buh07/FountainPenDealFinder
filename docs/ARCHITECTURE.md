# Architecture Summary

## Implemented shape

The project follows a hybrid internal-API architecture with adapters and MCP-facing wrappers.

### Ingestion layer

- Source adapters: Yahoo Auctions, Yahoo Flea Market, Mercari, Rakuma.
- Per-source fixture fallback for resilience and local development.
- Reliability guardrails in pipeline orchestration:
	- retry/backoff
	- parse-completeness filtering
	- source-level fallback behavior

### Processing layer

- Classification: rule-based brand/line/condition/lot decomposition.
- Pricing: resale + auction prediction with baseline artifact support and heuristic fallback.
- Training pipeline: dataset build, baseline artifact training, and evaluation-gate report.
- Proxy/coupon engine: DB-backed `proxy_pricing_policy` and `coupon_rule` tables.
- Scoring: weighted confidence model and bucket assignment.

### Persistence layer

Core operational tables:

- `raw_listing`
- `classification_result`
- `valuation_prediction`
- `auction_prediction`
- `proxy_option_estimate`
- `deal_score`
- `report_run`, `report_item`

Feedback, snapshots, and policy tables:

- `listing_snapshot`
- `listing_image`
- `manual_review`
- `training_example`
- `proxy_pricing_policy`
- `coupon_rule`

### API layer

Implemented routes include:

- Collection runs and ending-auction refresh
- Health checks and rolling-window health metrics
- Listing rank and listing detail
- Prediction and scoring
- Proxy deal views
- Daily report views
- Manual review ingestion
- Baseline retrain job trigger

### Monitoring layer

- `apps/api/app/services/monitoring.py` computes rolling-window health metrics from persisted listings, scores, and manual review outcomes.
- `GET /health/metrics` emits source volume, parse completeness, non-discard rate, false-positive rate, baseline-eval gate state, and alert keys.
- `POST /health/alerts/dispatch` posts current alert payloads to a configured webhook through `apps/api/app/services/alerting.py`.
- Alert thresholds are env-configurable through `MONITORING_*` settings.

### Worker and scheduling

- One-shot full run and one-shot ending-refresh modes.
- Recurring daemon mode with separate cadence for full-source runs and ending-auction refresh runs.
- Optional worker-side alert dispatch can be enabled to emit monitoring alerts after each run.

### MCP-facing services

- `apps/mcp-browser/src/index.js`: API-backed stdio JSON wrapper for listing search/detail and ending refresh trigger.
- `apps/mcp-pricing/src/index.js`: API-backed stdio JSON wrapper for prediction/scoring/retrain tools.

## Current constraints

- MCP wrappers are functional but not yet full MCP SDK servers.
- Classification remains text-first without image model integration.
- Baseline model training is lightweight and relies on small seed datasets.

## Next architectural steps

1. Add alert dedupe/rate-limiting and persistent alert-event history.
2. Add artifact promotion/versioning workflow after eval gate pass.
3. Move MCP wrappers to full MCP SDK tool registration.
4. Add review analytics and calibration feedback loop.
