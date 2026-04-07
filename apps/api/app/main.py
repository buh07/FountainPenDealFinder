from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import init_db
from .routers.collect import router as collect_router
from .routers.health import router as health_router
from .routers.listings import router as listings_router
from .routers.predict import router as predict_router
from .routers.proxy import router as proxy_router
from .routers.reports import router as reports_router
from .routers.scoring import router as scoring_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="FountainPenDealFinder API",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(collect_router)
app.include_router(listings_router)
app.include_router(scoring_router)
app.include_router(predict_router)
app.include_router(proxy_router)
app.include_router(reports_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "fountain-pen-api", "status": "running"}
