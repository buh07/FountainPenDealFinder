from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import get_settings
from .db import init_db
from .routers.collect import router as collect_router
from .routers.health import router as health_router
from .routers.listings import router as listings_router
from .routers.predict import router as predict_router
from .routers.proxy import router as proxy_router
from .routers.review import router as review_router
from .routers.retrain import router as retrain_router
from .routers.reports import router as reports_router
from .routers.scoring import router as scoring_router
from .routers.taxonomy import router as taxonomy_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="FountainPenDealFinder API",
    version="0.2.0",
    lifespan=lifespan,
)

settings = get_settings()
allow_origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
if not allow_origins:
    allow_origins = ["http://localhost:3000"]
allow_credentials = "*" not in allow_origins
allow_methods = [method.strip().upper() for method in settings.cors_allow_methods.split(",") if method.strip()]
if not allow_methods:
    allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
allow_headers = [header.strip() for header in settings.cors_allow_headers.split(",") if header.strip()]
if not allow_headers:
    allow_headers = ["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=allow_credentials,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
)

app.include_router(health_router)
app.include_router(collect_router)
app.include_router(listings_router)
app.include_router(scoring_router)
app.include_router(predict_router)
app.include_router(proxy_router)
app.include_router(review_router)
app.include_router(retrain_router)
app.include_router(reports_router)
app.include_router(taxonomy_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "fountain-pen-api", "status": "running"}
