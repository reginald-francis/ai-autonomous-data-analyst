from fastapi import FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import logging
import os
import asyncio

from src.config import get_settings
from src.routes.ask import router as ask_router
from src.services.session_service import cleanup_expired_sessions, cleanup_orphaned_files

load_dotenv()  # reads variables from a .env file and sets them in os.environ

# log info (more standard) instead of print()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__) 

# Background task: clean up expired sessions every 5 minutes
async def session_cleanup_loop():
    while True:
        await asyncio.sleep(300)  # wait 5 minutes
        cleanup_expired_sessions()

# Build FAISS index at startup function
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    os.makedirs(settings.uploads_dir, exist_ok=True)
    os.makedirs(settings.charts_dir, exist_ok=True)
    cleanup_orphaned_files()
    asyncio.create_task(session_cleanup_loop())
    logger.info("Session cleanup background task started")
    yield


app = FastAPI(
    title="Autonomous Data Analyst",
    description="AI powered data analysis agent",
    version="0.1.0",
    lifespan=lifespan
)

# Clean 400 for malformed requests (e.g. a string sent where a file is expected)
# instead of FastAPI's default verbose 422 with internal Pydantic error details.
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"Request validation failed: {exc.errors()}")
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request. Please check the fields you submitted."}
    )

# Global exception handler (safety net for any unhandled exceptions)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unexpected error: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again later."}
    )

app.include_router(ask_router)

# Serves generated chart PNGs at GET /charts/{filename} — needed so a remote
# caller (anything other than someone with filesystem access to the server)
# can actually fetch the chart_url an /upload response returns. check_dir=
# False because charts_dir is only created a moment later, in lifespan();
# StaticFiles would otherwise raise at import time if the folder doesn't
# exist yet. File names are random UUIDs (see chart_agent.py), never
# session_id, so this route can't be used to guess or enumerate another
# session's chart — see docs/THREAT_MODEL.md.
app.mount(
    "/charts",
    StaticFiles(directory=get_settings().charts_dir, check_dir=False),
    name="charts",
)

@app.get("/")
def root():
    logger.info("Root endpoint called")
    return {"message": "Welcome to the Autonomous Data Analyst API!"}


@app.get("/health")
def health_check():
    logger.info("Health check called")
    return {"status": "ok", "version": "0.1.0"}
