FROM python:3.13-slim

# Fail fast, don't buffer logs, don't cache pip downloads - all just
# housekeeping for a container that gets rebuilt from scratch every time,
# never patched in place. MPLCONFIGDIR points matplotlib's font cache at
# /tmp, which is writable by any user, so the cache-priming step below and
# the actual app user never fight over permissions on the same folder.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

# Installed before the source code so Docker's layer cache can skip this
# step entirely on a rebuild that only changed application code, not deps.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ src/
COPY models/ models/

# Chart PNGs and uploaded CSVs get written here at request time - see
# src/config.py's uploads_dir/charts_dir. Created and owned by the
# unprivileged 'app' user before we ever switch to running as it.
RUN useradd --create-home app \
    && mkdir -p data/uploads data/charts \
    && chown -R app:app /app

USER app

# Building matplotlib's font cache now, at image-build time, means the
# first real request doesn't pay that one-time cost - predictable cold
# starts matter more on Cloud Run than on a long-lived local server.
RUN python -c "import matplotlib.pyplot"

EXPOSE 8080

# Docker-level only - Cloud Run does its own independent health probing
# and ignores this. Kept so local `docker run` testing and CI's smoke test
# can ask "is this container healthy?" without separate polling logic.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# --workers 1: session_service.py's session store is an in-memory dict.
# A second worker process would have its own, empty copy, so a follow-up
# request landing on a different worker than its first request would
# incorrectly see "session not found". Cloud Run's PORT env var overrides
# the 8080 default; the fallback is for local `docker run` testing.
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
