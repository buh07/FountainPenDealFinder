FROM python:3.13-slim

WORKDIR /app

COPY apps/api/requirements.txt /app/apps/api/requirements.txt
COPY apps/worker/requirements.txt /app/apps/worker/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/apps/api/requirements.txt -r /app/apps/worker/requirements.txt

COPY . /app

ENV PYTHONPATH=/app

CMD ["python3", "-m", "uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
