# Patch aplicado no worktree ClimaticSystem (imagem `efc-ingestion:latest`)

## `downloads/download_merge_efc_last3d.py`
- Timeout HTTP 180s
- Fallback GCS `gs://efc-clima-data/obs/merge/grib/`
- Upload automático para GCS após download CPTEC

## `gcp/run_daily_pipeline.py` → `step_merge`
- `MERGE_DOWNLOAD_DAYS` (default 5) + `--no-extract` no download
- Aborta se `data/merge_grib` ficar sem `*.grib2` (não chama load às cegas)

Deploy: Cloud Build no worktree → `gcr.io/climaticsystem/efc-ingestion:latest`
(build `f3bc0ba7-9284-4356-b213-d6aefad9e5f6`, 19/11/2026 America/Sao_Paulo).
