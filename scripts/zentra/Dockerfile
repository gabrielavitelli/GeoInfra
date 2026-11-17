# Baked image for Cloud Run Job `zentra-ingest` (cheap: build once, run every 6h).
# Build:
#   gcloud builds submit --tag gcr.io/climaticsystem/zentra-ingest \
#     --project=climaticsystem scripts/zentra
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Repo layout expected by `python -m scripts.zentra.ingest`
COPY requirements.txt /app/scripts/zentra/requirements.txt
RUN pip install --no-cache-dir -r /app/scripts/zentra/requirements.txt

COPY __init__.py /app/scripts/__init__.py
COPY zentra/ /app/scripts/zentra/

# Parent package marker when building from scripts/ context — see deploy_job.sh
# which uses repo-root context. This Dockerfile supports both layouts via PATH.
ENV PYTHONPATH=/app

ENTRYPOINT ["python", "-m", "scripts.zentra.ingest"]
CMD ["run", "--skip-stale"]
