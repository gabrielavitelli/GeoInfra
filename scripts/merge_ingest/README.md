# MERGE ingest — cache GCS + catch-up

## Problema

O orchestrator falhava com `Nenhum JSON MERGE encontrado` quando o download CPTEC
não gravava GRIBs em `data/merge_grib` (timeout / falha transitória). O load
seguia mesmo assim e o extrator achava a pasta vazia.

## Mitigações em produção (19/11/2026)

1. Job dedicado `efc-ingestion-merge` + scheduler `efc-ingestion-merge-daily` (`15 8 * * *` UTC)
2. Timeouts `PIPELINE_TIMEOUT_MERGE=900` / `PIPELINE_TIMEOUT_MERGE_LOAD=900`
3. Cache GCS `gs://efc-clima-data/obs/merge/grib/`
4. Imagem `gcr.io/climaticsystem/efc-ingestion:latest` com:
   - download HTTP→FTP→GCS + stage automático
   - `step_merge` aborta se não houver GRIB2 (ver `patches/STEP_MERGE.md`)

## Stage manual (desta pasta)

```bash
pip install -r scripts/merge_ingest/requirements.txt
python3 scripts/merge_ingest/stage_grib_to_gcs.py --days 5
gcloud run jobs execute efc-ingestion-merge --project=climaticsystem --region=us-central1 --wait
```
