# RESULTS

Last updated: 2026-04-09
Current branch: main
Latest pushed commit at status refresh: b289bae

## Pipeline Completion Summary

The project now has a V2 reliability + spec-gap delivery baseline: multi-market ingestion with guardrails, model artifact versioning/pointer lifecycle, exact proxy coupon optimization, local object-store asset capture, multi-stage text+image-aware classification fallback, priority auction polling, MCP SDK stdio servers, and expanded deterministic test coverage.

Implemented end-to-end flow:

1. Collect listings from Yahoo Auctions, Yahoo Flea Market, Mercari, and Rakuma.
2. Apply retry/backoff, parse-completeness checks, and price-parse-repair attempts before persistence.
3. Normalize and upsert into `raw_listing` with dedupe by source + source_listing_id.
4. Persist listing snapshots and listing images for historical tracking.
5. Run classification + condition extraction.
6. Run resale/auction prediction with trained-baseline artifact support and fallback heuristics.
7. Estimate proxy/coupon costs through DB-backed policy + rule tables.
8. Resolve listing `price_status` (`valid`, `missing`, `parse_error`) and enforce guardrail bucketing.
9. Score and bucket into `confident`, `potential`, or `discard`.
10. Generate daily markdown report with report-window filtering and data-quality annotations.
11. Accept manual review actions and persist training examples.
12. Normalize ingestion + resale-training rows into shared canonical brand/line/category/condition bins and incorporate feedback pricing rows.

## Status Matrix

| Pipeline Area | Status | What is implemented | What is left |
| --- | --- | --- | --- |
| Source ingestion | Partial | Four marketplace adapters are active; retries/backoff + parse completeness checks are in `apps/api/app/services/pipeline.py`. Adapters now mark `raw_attributes.price_parse_error` and attempt detail-based price repair. | Add anti-block mitigation and fixture refresh automation for selector drift. |
| Normalization | Partial | Canonical upsert and dedupe by source/source_listing_id are stable. | Expand source-specific normalization (seller, shipping, fee, provenance details). |
| Classification | Partial | Multi-stage classifier (`apps/api/app/services/classification_pipeline.py`) now runs text extraction, optional image disambiguation, lot decomposition, taxonomy/condition normalization, and uncertainty tagging with text-only fallback. | Improve image stage with stronger embeddings and calibration labels. |
| Resale valuation | Partial | `apps/api/app/services/pricing_models.py` now resolves active artifacts through version pointers and keeps heuristic fallback. | Add richer feature engineering and confidence calibration curves. |
| Auction prediction | Partial | Pointer-based artifact loading and gated retrain promotion are active for auction artifacts as well. | Add richer auction dynamics features and calibration tracking. |
| Proxy/coupon engine | Partial | `proxy_pricing_policy` + `coupon_rule` tables drive deal-cost estimation via `apps/api/app/services/proxy_tracker.py`; coupon selection is now exact and deterministic under stackability constraints. | Build admin sync/update flow for policy rules and coupon lifecycle. |
| Deal scoring | Partial | Confidence-weighted scoring now enforces `price_status` guardrails: `missing` prices are forced to `discard`, unresolved `parse_error` rows are kept low-confidence `potential` with neutralized profit. | Tune weights with outcome data from review loop and realized results. |
| Storage and schema | Partial | Added migrations through `alembic/versions/e1c4a2b9d7f0_add_listing_asset_table.py` for snapshots, images, listing asset metadata, policies/coupons, reviews/training examples, and alert history. | Add retention/compaction jobs and optional object-store backends (S3/R2). |
| Reporting | Partial | Daily report generation now filters fixed-price listings by report-date local-day window and auctions by rolling `+24h` with known `ends_at`; markdown includes data-quality annotations; listing summary assembly now bulk-loads related rows to avoid N+1 query patterns. | Add richer ranking views and notification delivery guarantees. |
| Internal API | Partial | Existing routes remain stable; `ListingSummary` now includes `price_status` and `risk_flags` for transparency; `/listings` supports `limit` + `offset` pagination and total-count responses. | Add stricter request validation and job-status persistence. |
| Operations/deployment | Partial | Worker now supports `--once`, `--ending-refresh-once`, `--priority-refresh-once`, and recurring `--daemon` tiered cadence (standard + high-priority ending auctions); monitoring includes ingestion/retrain failure telemetry fields alongside existing health metrics and alert dispatch reliability. | Add multi-destination notification fanout with delivery analytics. |
| Manual review loop | Partial | Manual feedback is persisted to `manual_review` and mirrored to `training_example`; review payload now supports corrected brand/line/condition/pricing and taxonomy aliases, which can append feedback type aliases and pricing rows for retraining. | Add review history/edit UX and feedback analytics pipeline. |
| MCP services | Partial | `apps/mcp-browser/src/index.js` and `apps/mcp-pricing/src/index.js` now run on official MCP SDK stdio servers, preserving tool names/contracts and returning structured error envelopes. | Add richer typed outputs and integration smoke tests with installed node dependencies. |

## Validation Notes

Recent local validation confirmed:

- `python3 -m compileall apps/api/app apps/worker scripts` passes.
- `python3 -m pytest apps/api/tests -q` passes (44 tests), including parser regression (`price_parse_error` semantics), health metrics + failure telemetry, alert dispatch dedupe/history/retry, config validation, pagination behavior, taxonomy/feedback capture, price-quality gating, stale fixture fallback, report-window filtering, exact coupon optimizer behavior, model-version API rollback flow, object-store capture/dedupe, multi-stage classifier behavior, and priority polling selection/scheduler cadence.
- `node --check apps/mcp-browser/src/index.js && node --check apps/mcp-pricing/src/index.js` passes.
- Alembic upgrade chain through revision `e1c4a2b9d7f0` applies cleanly on SQLite smoke DB.
- Pipeline run + ending-auction refresh run succeed on smoke DB.
- API smoke checks pass for `/review/{listing_id}`, `/retrain/jobs`, and `/collect/refresh-ending`.
- API includes taxonomy standard endpoint `/taxonomy/standard` for canonical categories/types/conditions.
- `/health/metrics` returns rolling-window metrics and alert keys as expected.
- `/health/alerts/dispatch` returns webhook dispatch status (`sent`, `reason`, destination/status metadata, and dedupe/signature fields).
- Dataset and model scripts produce:
  - `data/labeled/pen_swap_sales.csv`
  - `data/labeled/yahoo_auction_outcomes.csv`
  - `models/resale/baseline_v1.json`
  - `models/yahoo-auction/baseline_v1.json`
  - `models/eval/baseline_eval_v1.json`
- Evaluation gate run passes on current baseline dataset.

## Next Priority Work

1. Expand historical datasets (Pen_Swap + Yahoo outcomes) and add richer eval-report trend tracking.
2. Add stronger image embeddings + optional thumbnail generation backend and benchmark disambiguation gain.
3. Build review history and outcome analytics UI to drive score calibration.
4. Add anti-block parser hardening and fixture auto-refresh workflow.
5. Add multi-destination alert fanout with retry/backoff and delivery health metrics.
