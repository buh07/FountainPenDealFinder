from fastapi import FastAPI

from .routers.health import router as health_router
from .routers.reports import router as reports_router

app = FastAPI(title="FountainPenDealFinder API", version="0.1.0")

app.include_router(health_router)
app.include_router(reports_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "fountain-pen-api", "status": "running"}
