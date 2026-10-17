from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.datasets import router as dataset_router
from app.db import get_session
from app.middleware import BodyLimitMiddleware

app = FastAPI(title="DataPilot API", version="0.1.0")
app.add_middleware(BodyLimitMiddleware, max_bytes=get_settings().max_upload_bytes + 65536)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(dataset_router)


@app.exception_handler(SQLAlchemyError)
async def database_error(_, __):
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": "storage_unavailable", "message": "业务数据库暂不可用。"}},
    )


@app.get("/api/health/live")
def live():
    return {"status": "ok", "service": "datapilot-api"}


@app.get("/api/health/ready")
def ready(session: Annotated[Session, Depends(get_session)]):
    try:
        session.execute(text("SELECT 1"))
        # Checks migrations as well as connectivity.
        session.execute(text("SELECT id FROM datasets LIMIT 0"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "database": "unavailable"}
        )
    return {"status": "ok", "database": "ok"}
