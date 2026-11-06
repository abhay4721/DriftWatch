"""FastAPI application foundation and operational endpoints."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from driftwatch.api_v1 import router as api_v1_router
from driftwatch.config import settings
from driftwatch.database import get_db
from driftwatch.logging_config import configure_logging

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
HTTP_REQUESTS = Counter("driftwatch_http_requests_total", "HTTP requests served by DriftWatch")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("DriftWatch API starting")
    yield
    logger.info("DriftWatch API stopping")


app = FastAPI(
    title="DriftWatch API",
    description="Local-first infrastructure drift detection platform.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_v1_router)


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        error = exc.detail
    else:
        error = {"code": "http_error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": error}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "The request did not match the expected schema",
                "details": details,
            }
        },
    )


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    """Indicate that the API process is running."""
    HTTP_REQUESTS.inc()
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    """Indicate that the API and database can accept requests."""
    HTTP_REQUESTS.inc()
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Database readiness check failed")
        raise HTTPException(
            status_code=503,
            detail={"code": "database_unavailable", "message": "Database is unavailable"},
        ) from exc
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
