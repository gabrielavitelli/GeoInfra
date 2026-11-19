FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libeccodes0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-score.txt .
RUN pip install --no-cache-dir -r requirements-score.txt

COPY __init__.py check.py score_status.py operational_score.py daily_job.py ./

ENV PYTHONUNBUFFERED=1
ENV GCP_PROJECT=climaticsystem
ENV BQ_DATASET=efc_clima
ENV GCS_BUCKET=efc-clima-data

ENTRYPOINT ["python", "daily_job.py"]
