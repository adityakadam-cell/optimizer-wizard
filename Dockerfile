# syntax=docker/dockerfile:1.7

# ---------- builder ----------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---------- runtime ----------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Run as a non-root user.
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

# Copy installed deps from builder, then app source.
COPY --from=builder /install /usr/local
COPY --chown=app:app . .

USER app

EXPOSE 8000

# Health check uses /healthz endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/healthz').read()" || exit 1

# Single worker is intentional — see app.py / README.md.
# --timeout 200 because PageSpeed audits can take 90+ seconds.
CMD ["sh", "-c", "gunicorn wsgi:app --workers 1 --threads 4 --bind 0.0.0.0:${PORT} --timeout 200 --access-logfile -"]
