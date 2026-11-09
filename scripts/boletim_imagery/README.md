# Boletim km imagery (~2 m)

Refresh snapshots of high-res satellite (Esri World Imagery, ~1–2 m) for each EFC km into GCS:

`gs://efc-clima-data/monitor/boletim-imagery/km_XXXX.jpg`

No proprietary NODIS/orthophoto bucket was available in this project; Esri World Imagery is the operational ~2 m source used by the monitor 3D/2D basemap and boletim cards.

## Manual

```bash
cd _monitor_deploy   # or set PYTHONPATH to the monitor tree
python scripts/refresh_boletim_km_imagery.py --step 5
```

## Scheduler (every 10 days)

```bash
./scripts/boletim_imagery/setup_boletim_imagery_scheduler.sh climaticsystem us-central1
```
