# D0108 Visão Geral map — investigation

## Verdict
**D0108 does not exist** as a BNDMET/ANA station code (national API `tipo=todas` ~3900 stations; EFC CSV catalogs; `clima_series`; `locations`). Closest real codes: D6108 (AM), D7108 (SP), D8108 (DF) — none on the EFC corridor.

Live map embed (rev `monitor-efc-prod-00158-qtp`) already has 43 BNDMET codes including Parauapebas **D6182**, **D6170**, **82567**, **A230**. The only “0108” string in map HTML is fitBounds lat `-6.101085…`.

## Root cause (for “why isn’t D0108 on the map?”)
Not a filter bug for this code — **no lat/lon / no catalog row / no precip series** for D0108, so nothing to plot.

## Related hardening applied (helps other missing/stale markers)
1. `_load_station_catalog_from_bq`: stop requiring precip within 30 days; keep stations that have coords in `locations`.
2. Map markers: old `last_obs` no longer `continue`-hidden; shown as **stale gray**.
3. Restored `locations` = railway_km + 125 `estacao_*` from CSV catalogs via `scripts/sync_locations_bq.py`.
4. Redeploy with `MONITOR_MAP_CACHE_VER` bust; preserve Previsão yellow line + USER2–USER6; keep entorno card from sibling tree.

## If user meant another station
Near Parauapebas (~4 stations): D6182, 82567 CARAJÁS, A230, (+ D6170/D6168 ~99 km). Ask them to confirm the code.
