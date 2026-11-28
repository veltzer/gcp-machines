# Container image for the Cloud Run service (built by gcloud_deploy.sh from utils-bash via
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
# The deploy stamp is written by gcloud_deploy.sh just before deploying;
# building without it fails on purpose so an unstamped image never ships.
COPY build_info.json ./
# when the image was built (UTC), for the About page; after the stamp, which
# is new on every deploy, so this layer is never a cached one
RUN date --utc --iso-8601=seconds > build_time

ENV PATH="/app/.venv/bin:$PATH"

# Cloud Run tells us which port to listen on via $PORT.
CMD ["sh", "-c", "exec gunicorn --bind :${PORT} --workers 1 --threads 8 --timeout 0 src.main:app"]
