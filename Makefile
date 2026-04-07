.PHONY: api worker dashboard up down fmt

api:
	uvicorn apps.api.app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	python -m apps.worker.worker --once

dashboard:
	python -m http.server 8080 -d apps/dashboard/public

up:
	docker compose up -d

down:
	docker compose down

fmt:
	python -m pip install black >/dev/null 2>&1 || true
	black apps
