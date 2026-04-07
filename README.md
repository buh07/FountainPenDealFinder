# FountainPenDealFinder

Personal deal-finding system for Japanese fountain-pen marketplaces.

## Monorepo Layout

- `apps/api`: FastAPI internal API
- `apps/worker`: scheduled collection and scoring worker
- `apps/dashboard`: lightweight review UI scaffold
- `apps/mcp-browser`: MCP marketplace browser tool scaffold (TypeScript)
- `apps/mcp-pricing`: MCP pricing/deal tool scaffold (TypeScript)
- `packages/*`: shared modules and domain contracts
- `data/*`: fixtures, taxonomy, labels, generated reports
- `models/*`: model artifact placeholders
- `infra/*`: local infrastructure files
- `docs/*`: architecture and setup documentation

## Current API Endpoints

- `GET /health`
- `POST /collect/run`
- `GET /listings`
- `POST /score/{listing_id}`
- `POST /predict/resale/{listing_id}`
- `POST /predict/auction/{listing_id}`
- `GET /reports/daily/{date}`

## Source Ingestion Status

- Yahoo! JAPAN Auctions: connected via `YahooAuctionsAdapter` in `apps/api/app/adapters/yahoo_auctions.py`
- Fallback source: fixture data in `data/fixtures/listings_sample.json` when Yahoo data is unavailable
- Ingestion order: Yahoo Auctions first, fixture fallback second

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

1. Open dashboard scaffold:

```bash
python -m http.server 8080 -d apps/dashboard/public
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
