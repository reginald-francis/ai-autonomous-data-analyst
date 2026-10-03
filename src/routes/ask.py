from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from uuid import uuid4
from src.services.analyst_service import analyse
from src.services.session_service import create_session, get_session
from src.services.rag_service import build_session_index
from src.config import get_settings
from src.utils.schemas import AnalysisResponse
import logging
import os
import pandas as pd

logger = logging.getLogger(__name__)

router = APIRouter()


class AskRequest(BaseModel):
    question: str
    file_path: str


def _resolve_within_project_root(file_path: str) -> str:
    """
    Resolves file_path against the current working directory and rejects it
    if the resolved path escapes that directory — e.g. "../../../etc/passwd"
    or an absolute path elsewhere on disk. Raises HTTPException(403) on
    escape. Returns the resolved absolute path on success.

    The project root (cwd) is the boundary, not data/uploads/, because /ask
    is documented as a local dev/testing endpoint that reads arbitrary
    project-relative paths (sample_data.csv, tests/data/*.csv), not just
    uploaded files.
    """
    root = os.path.realpath(os.getcwd())
    resolved = os.path.realpath(os.path.join(root, file_path))
    if os.path.commonpath([root, resolved]) != root:
        raise HTTPException(
            status_code=403,
            detail="file_path must resolve to a location inside the project directory."
        )
    return resolved


@router.post("/ask", response_model=AnalysisResponse)
def ask_question(request: AskRequest):
    if not get_settings().enable_ask_endpoint:
        raise HTTPException(
            status_code=404,
            detail="This endpoint is disabled. Use POST /upload instead."
        )

    logger.info("Request received: POST /ask")
    logger.info(f"Question: {request.question} | File: {request.file_path}")
    resolved_path = _resolve_within_project_root(request.file_path)
    result = analyse(request.question, resolved_path)
    logger.info(f"Request completed | Status: {result.status} | Attempts: {result.attempts} | Time: {result.time_taken}")
    return result


@router.post("/upload", response_model=AnalysisResponse)
async def upload_and_ask(
    request: Request,
    question: str = Form(...),
    session_id: str = Form(None),
    file: UploadFile = File(None),
    context_file: UploadFile = File(None),
):
    """
    Ask a question about a CSV file. Two ways to use this endpoint:

    1. First request — upload a CSV file and ask a question.
       The response includes a session_id. Save it for follow-up questions.

    2. Follow-up request — send session_id + question, no file needed.
       The server reuses the previously uploaded file for the session duration (30 min).

    Optional in either case: context_file — a plain-text/Markdown document
    describing business context specific to this dataset (e.g. column
    glossary, terminology, fiscal calendar) that a developer could never
    pre-write. If provided, it's chunked and embedded into a RAG index
    scoped to this session only, and relevant chunks are retrieved for each
    question asked in the session. Omitting it changes nothing — no context
    document means no business context is added to any prompt.
    """
    logger.info(f"Request received: POST /upload | session_id={session_id}")

    # FastAPI's `file: UploadFile = File(None)` binds to whichever file the
    # client sent under the "file" field — if a client sends more than one
    # under that same field name (Postman allows this; a normal browser
    # file-input cannot), FastAPI/Starlette silently binds only one of them
    # and the rest are discarded with no error at all. Inspecting the raw
    # form here catches that before it can silently drop data.
    form = await request.form()
    file_entries = form.getlist("file")
    if len(file_entries) > 1:
        raise HTTPException(
            status_code=400,
            detail="Only one file may be uploaded per request. Please send a single file."
        )
    context_file_entries = form.getlist("context_file")
    if len(context_file_entries) > 1:
        raise HTTPException(
            status_code=400,
            detail="Only one context document may be uploaded per request."
        )

    # --- Follow-up request: reuse existing session ---
    if session_id:
        session = get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail="Session not found or expired. Please upload your file again."
            )
        file_path = session["file_path"]
        original_filename = session["original_filename"]
        logger.info(f"Reusing session {session_id} | file: {original_filename}")

    # --- First request: validate and save uploaded file ---
    else:
        if file is None:
            raise HTTPException(status_code=400, detail="No file uploaded. Please provide a CSV file.")

        if not file.filename.endswith(".csv"):
            raise HTTPException(status_code=400, detail="Only CSV files are supported.")

        # await: read all bytes from the upload.
        # The 'await' keyword means: pause here until reading is done,
        # but let other requests run in the meantime (non-blocking).
        content = await file.read()

        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        max_file_size = get_settings().max_file_size
        if len(content) > max_file_size:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds the {max_file_size // (1024 * 1024)}MB limit."
            )

        # Generate a unique ID upfront so the filename and session_id match from the start
        session_id = str(uuid4())
        file_path = f"{get_settings().uploads_dir}/{session_id}.csv"

        with open(file_path, "wb") as f:
            f.write(content)

        # Validate it's a parseable CSV (nrows=0 just reads headers — fast)
        try:
            pd.read_csv(file_path, nrows=0)
        except Exception:
            os.remove(file_path)
            raise HTTPException(status_code=400, detail="File is not a valid CSV.")

        # Register the session with the already-saved file path
        original_filename = file.filename
        create_session(session_id, file_path, original_filename)
        logger.info(f"File saved: {file_path} | original: {original_filename}")

    # --- Optional business-context document (either request type) ---
    if context_file is not None:
        context_bytes = await context_file.read()

        if len(context_bytes) == 0:
            raise HTTPException(status_code=400, detail="Context document is empty.")

        max_context_doc_size = get_settings().max_context_doc_size
        if len(context_bytes) > max_context_doc_size:
            raise HTTPException(
                status_code=400,
                detail=f"Context document exceeds the {max_context_doc_size // (1024 * 1024)}MB limit."
            )

        try:
            context_text = context_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Context document must be plain text or Markdown (UTF-8)."
            )

        build_session_index(session_id, context_text, filename=context_file.filename)
        logger.info(f"Session {session_id}: indexed context document ({context_file.filename})")

    result = analyse(question, file_path, session_id=session_id, original_filename=original_filename)
    logger.info(f"Request completed | Status: {result.status} | Attempts: {result.attempts} | Time: {result.time_taken}")
    return result
