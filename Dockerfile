# Container image for the Cloud Run service (built by scripts/deploy.sh via
# `gcloud run deploy --source`). Dependencies come from uv.lock, the single
# place versions are controlled.

FROM python:3.14-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies before copying the code so code-only changes reuse
# this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src

ENV PATH="/app/.venv/bin:$PATH"

# Cloud Run tells us which port to listen on via $PORT.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 src.main:app
