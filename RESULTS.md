# RESULTS

Last updated: 2026-04-07
Current branch: main
Latest pushed commit: b62de65

## Pipeline Completion Summary

The project now has a working V1.5 baseline: multi-market ingestion, reliability guardrails, scoring/reporting, DB-driven proxy rules, review capture, snapshot storage, baseline training scripts, and API-wrapped MCP tools.

Implemented end-to-end flow:

1. Collect listings from Yahoo Auctions, Yahoo Flea Market, Mercari, and Rakuma.
2. Apply retry/backoff and parse-completeness checks before persistence.
3. Normalize and upsert into `raw_listing` with dedupe by source + source_listing_id.
4. Persist listing snapshots and listing images for historical tracking.
5. Run classification + condition extraction.
6. Run resale/auction prediction with trained-baseline artifact support and fallback heuristics.
7. Estimate proxy/coupon costs through DB-backed policy + rule tables.
8. Score and bucket into `confident`, `potential`, or `discard`.
9. Generate daily markdown report and expose API output.
10. Accept manual review actions and persist training examples.

## Status Matrix

| Pipeline Area | Status | What is implemented | What is left |
| --- | --- | --- | --- |
| Source ingestion | Partial | Four marketplace adapters are active; retries/backoff + parse completeness checks and per-source fixture fallback are in `apps/api/app/services/pipeline.py`. | Harden parser selectors with fixture regression tests and anti-block mitigation. |
| Normalization | Partial | Canonical upsert and dedupe by source/source_listing_id are stable. | Expand source-specific normalization (seller, shipping, fee, provenance details). |
| Classification | Partial | Rule-based classification, condition flags, lot estimation, and confidence components in `apps/api/app/services/pipeline.py`. | Add image-assisted taxonomy resolver and richer uncertainty labels. |
| Resale valuation | Partial | `apps/api/app/services/pricing_models.py` now reads baseline artifact `models/resale/baseline_v1.json` with heuristic fallback. | Scale historical data, evaluate calibration, add promotion/versioning workflow. |
| Auction prediction | Partial | `apps/api/app/services/pricing_models.py` reads baseline artifact `models/yahoo-auction/baseline_v1.json` with fallback logic. | Add richer feature set and lower-tail calibration monitoring. |
| Proxy/coupon engine | Partial | `proxy_pricing_policy` + `coupon_rule` tables drive deal-cost estimation via `apps/api/app/services/proxy_tracker.py`. | Build admin sync/update flow for policy rules and coupon lifecycle. |
| Deal scoring | Partial | Confidence-weighted profit scoring and bucketing are persisted and report-ready. | Tune weights with outcome data from review loop and realized results. |
| Storage and schema | Partial | Added migration `alembic/versions/9d51f7c2a6e1_add_review_snapshot_and_policy_tables.py` for snapshots, images, policies, coupons, reviews, training examples. | Add artifact metadata table and retention/compaction jobs. |
| Reporting | Partial | Daily markdown report generation and persisted report items are stable. | Add notifier integrations and richer ranking views. |
| Internal API | Partial | Added `POST /collect/refresh-ending`, `GET /listings/{listing_id}`, `POST /review/{listing_id}`, `POST /retrain/jobs` in addition to existing routes. | Add stricter request validation, pagination contracts, and job-status persistence. |
| Operations/deployment | Partial | Worker supports `--once`, `--ending-refresh-once`, and recurring `--daemon` schedule with separate cadences. | Add production scheduler supervision, metrics, and alerting. |
| Manual review loop | Partial | Manual feedback is persisted to `manual_review` and mirrored to `training_example`; dashboard can submit review actions. | Add review history/edit UX and feedback analytics pipeline. |
| MCP services | Partial | `apps/mcp-browser/src/index.js` and `apps/mcp-pricing/src/index.js` now provide real API-backed stdio JSON tool wrappers. | Migrate to full MCP SDK server registration and richer typed tool contracts. |

## Validation Notes

Recent local validation confirmed:

- `python -m compileall apps/api/app apps/worker scripts` passes.
- Alembic upgrade chain through revision `9d51f7c2a6e1` applies cleanly on SQLite smoke DB.
- Pipeline run + ending-auction refresh run succeed on smoke DB.
- API smoke checks pass for `/review/{listing_id}`, `/retrain/jobs`, and `/collect/refresh-ending`.
- Dataset and model scripts produce:
  - `data/labeled/pen_swap_sales.csv`
  - `data/labeled/yahoo_auction_outcomes.csv`
  - `models/resale/baseline_v1.json`
  - `models/yahoo-auction/baseline_v1.json`

## Next Priority Work

1. Add parser regression test suite with source HTML fixtures and completeness assertions.
2. Add ingestion/model drift metrics and alert thresholds.
3. Expand historical datasets (Pen_Swap + Yahoo outcomes) and add train/eval report gating.
4. Implement full MCP SDK servers for browser/pricing services.
5. Build review history and outcome analytics UI to drive score calibration.
