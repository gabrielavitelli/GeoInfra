# Slim Orion precip ingest image — deps baked (no pip on each hourly run).
FROM python:3.11-slim

WORKDIR /app
RUN mkdir -p /app/scripts && printf '' > /app/scripts/__init__.py
COPY requirements.txt /app/scripts/orion_ews/requirements.txt
RUN pip install --no-cache-dir -r /app/scripts/orion_ews/requirements.txt

COPY . /app/scripts/orion_ews/
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default: cheap hourly precip refresh (override args on the Job if needed).
ENTRYPOINT ["python", "-m", "scripts.orion_ews.ingest"]
CMD ["readings", "--precip-only", "--days", "1", \
     "--bq-project", "climaticsystem", \
     "--bq-dataset", "efc_clima", \
     "--gcs-prefix", "gs://efc-clima-data/obs/ews/", \
     "--no-from-secret"]
