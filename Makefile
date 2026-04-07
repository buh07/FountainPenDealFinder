.PHONY: api worker dashboard up down db-upgrade db-downgrade db-revision fmt

api:
	python3 -m alembic upgrade head
	python3 -m uvicorn apps.api.app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	python3 -m alembic upgrade head
	python3 -m apps.worker.worker --once

dashboard:
	python3 -m http.server 8080 -d apps/dashboard/public

up:
	docker compose up -d

down:
	docker compose down

db-upgrade:
	python3 -m alembic upgrade head

db-downgrade:
	python3 -m alembic downgrade -1

db-revision:
	python3 -m alembic revision --autogenerate -m "$(m)"

fmt:
	python3 -m pip install black >/dev/null 2>&1 || true
	black apps
