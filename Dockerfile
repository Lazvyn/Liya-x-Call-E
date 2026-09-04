# ─────────────────────────────────────────────────────────────────────────────
# Liya Agent Backend — Cloud Run Dockerfile
#
# Build:  docker build -t liya-backend .
# Run:    docker run -p 8080:8080 -e LIYA_API_KEY=dev liya-backend
# ─────────────────────────────────────────────────────────────────────────────

# Use a slim Python 3.12 image (matches Cloud Run's recommended base)
FROM python:3.12-slim

# Metadata
LABEL maintainer="Liya Agent"
LABEL description="Liya autonomous AI agent — Cloud Run backend"

# Prevent .pyc files and enable unbuffered stdout (important for Cloud Run logs)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Cloud Run sets PORT env-var; default to 8080
ENV PORT=8080

# Working directory inside the container
WORKDIR /app

# ── 1. Install system dependencies ──────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Install Python dependencies ──────────────────────────────────────────
# Backend-only subset (no PyQt6/pyautogui/playwright/mss — desktop-app deps
# main.py needs but this headless container never imports). See
# requirements-desktop.txt for the full local/desktop superset.
COPY requirements-backend.txt .
RUN pip install --no-cache-dir -r requirements-backend.txt

# ── 3. Copy project source ───────────────────────────────────────────────────
COPY agent/         ./agent/
COPY config/        ./config/
COPY memory/        ./memory/
COPY actions/       ./actions/
COPY backend/       ./backend/
COPY observability/ ./observability/

# ── 4. Expose port (documentation only — Cloud Run uses PORT env-var) ────────
EXPOSE 8080

# ── 5. Start the FastAPI server ───────────────────────────────────────────────
# uvicorn is the ASGI server; Cloud Run expects the app to bind 0.0.0.0:$PORT
CMD ["sh", "-c", "uvicorn backend.server:app --host 0.0.0.0 --port ${PORT} --log-level info"]