# Setup

## Local development

1. Start infra:

```bash
make up
```

2. Create and activate a virtual environment, then install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r apps/api/requirements.txt -r apps/worker/requirements.txt
```

3. Run API:

```bash
make api
```

4. Run worker scaffold:

```bash
make worker
```

5. Run static dashboard:

```bash
make dashboard
```
