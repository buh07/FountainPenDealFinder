# RESULTS

Last updated: 2026-04-07
Current branch: main
Latest pushed commit: 91c6a2d

## Pipeline Completion Summary

The pipeline has a working V1 path from ingestion to daily report generation across four JP marketplaces.

Implemented end-to-end flow:

1. Collect listings from Yahoo Auctions, Yahoo Flea Market, Mercari, and Rakuma.
2. Normalize and upsert listings into `raw_listing`.
3. Run rule-based classification and condition extraction.
4. Run heuristic resale and auction predictions via dedicated pricing-model service.
5. Estimate and rank proxy routes (Buyee, FromJapan, Neokyo) via dedicated proxy-tracker service.
6. Score opportunities and bucket into `confident`, `potential`, or `discard`.
7. Persist scores and generate a daily markdown report.
8. Expose results through API endpoints, including proxy deal endpoints.

## Status Matrix

| Pipeline Area | Status | What is implemented | What is left |
| --- | --- | --- | --- |
| Source ingestion | Partial | First-class adapters exist for Yahoo Auctions, Yahoo Flea Market, Mercari, and Rakuma (`apps/api/app/adapters/*`). Per-source fixture fallback is implemented in pipeline orchestration. | Harden parsing/selectors, anti-block handling, retry/backoff, and richer source-specific fields. |
| Normalization | Partial | Canonical raw listing persistence and dedupe keyed by source + source_listing_id in `apps/api/app/services/pipeline.py`. | Expand normalization to all source-specific attributes and richer shipping/seller fields. |
| Classification | Partial | Rule-based brand/line detection, lot count estimation, condition flags, and confidence components in `apps/api/app/services/pipeline.py`. | Replace/augment with taxonomy resolver + image model + uncertainty labels from full spec. |
| Resale valuation | Partial | Heuristic resale predictor with confidence and intervals in `apps/api/app/services/pricing_models.py`. | Train data-driven model on historical datasets and add calibration. |
| Auction prediction | Partial | Heuristic expected final and low-win predictions for auction listings in `apps/api/app/services/pricing_models.py`. | Train dedicated Yahoo auction models for expected close and lower-tail outcomes. |
| Proxy/coupon engine | Partial | Proxy cost/profit ranking and recommendation separated into `apps/api/app/services/proxy_tracker.py`, including persisted arbitrage rank and expected profit fields. | Move coupon logic to data-driven rule tables and versioned policy engine. |
| Deal scoring | Partial | Weighted confidence + profit thresholds + bucketing implemented and persisted. | Tune weights, thresholds, and risk penalties using observed outcomes. |
| Storage and schema | Partial | SQLAlchemy models plus Alembic revisions for baseline + proxy profitability fields (`alembic/versions/8de48d5c297e_initial_schema.py`, `alembic/versions/4f22d9c6f33d_add_proxy_profit_tracking_columns.py`). | Add snapshot history tables (`listing_snapshot`, etc.) and model artifact tracking. |
| Reporting | Partial | Daily markdown report generation and persisted report metadata in `apps/api/app/services/reporting.py`. | Add notifier integrations (Telegram/Discord/email) and richer ranking views. |
| Internal API | Partial | Endpoints implemented for collect, list, score, predict, proxy deal views, and daily report. | Add review/retrain endpoints and stronger validation contracts. |
| Operations/deployment | Partial | Postgres-first docker compose wiring and migration-first startup path are in place. | Add scheduler, monitoring metrics, alerts, and production secrets management. |
| Manual review loop | Not started | No manual correction UI/workflow yet beyond basic static dashboard scaffold. | Build review actions, feedback capture, and retraining hooks. |
| MCP services | Not started | MCP browser/pricing folders are scaffold-only. | Implement real MCP tool handlers and integrate with API services. |

## Validation Notes

Recent local validation confirmed:

- API modules compile cleanly via `python -m compileall apps/api/app`.
- Alembic revision chain upgrades cleanly in SQLite smoke test (`DATABASE_URL=sqlite:///./tmp_migration_check.db`).
- Pipeline run works end-to-end with fixture-backed multi-source ingestion and scoring.
- Proxy endpoint smoke test succeeds (`GET /proxy/top` returns ranked rows).

## Next Priority Work

1. Harden each marketplace parser against markup drift and anti-bot responses.
2. Add listing snapshot history to support model backtesting and drift analysis.
3. Replace heuristic resale model with a trained baseline model and calibration metrics.
4. Add automated tests for adapters, proxy ranking, and score bucketing.
5. Implement manual review endpoint plus persisted review feedback table.
