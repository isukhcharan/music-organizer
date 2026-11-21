FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/data

WORKDIR /app/backend

# keep in sync with backend/pyproject.toml (dependencies + the "providers" extra)
RUN pip install \
    "fastapi>=0.110" "uvicorn[standard]>=0.29" "mutagen>=1.47" \
    "ytmusicapi>=1.8" "musicapy>=2.0" "send2trash>=1.8"

COPY backend/musicorganizer ./musicorganizer

EXPOSE 8765
CMD ["python", "-m", "musicorganizer", "--db", "/data/state.db", "serve", "--no-browser", "--host", "0.0.0.0", "--port", "8765"]
