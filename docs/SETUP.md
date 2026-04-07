# Setup

## Local development

1. Copy environment template:

```bash
cp .env.example .env
```

1. Start infra:

```bash
make up
```

1. Create and activate a virtual environment, then install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r apps/api/requirements.txt -r apps/worker/requirements.txt
```

1. Run Alembic migrations:

```bash
make db-upgrade
```

1. Run API:

```bash
make api
```

1. Trigger collection/scoring run via API:

```bash
curl -X POST http://localhost:8000/collect/run
```

1. Run worker once:

```bash
make worker
```

1. Run static dashboard:

```bash
make dashboard
```

## Yahoo Auctions specific notes

1. Confirm connector settings in `.env`:

- `YAHOO_AUCTIONS_ENABLED=true`
- `YAHOO_AUCTIONS_KEYWORD=万年筆`
- `YAHOO_AUCTIONS_BASE_URL=https://auctions.yahoo.co.jp`

1. If your local Python SSL trust chain is incomplete, temporarily set `YAHOO_AUCTIONS_VERIFY_SSL=false` for development only.
