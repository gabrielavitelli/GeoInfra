-- Orion / EWS landing tables for climaticsystem.efc_clima
-- Run once (or via terraform/migration) before the ingest job appends.
-- Project: climaticsystem | Dataset: efc_clima
--
-- Design:
--   * orion_devices     — device catalog snapshot (map / battery / status)
--   * orion_sensors     — channel dictionary (raw + soft)
--   * orion_readings_staging — raw API payload (optional audit)
--   * clima_series      — EXISTING long table; append with source='orion'
--                         (monitor already filters by source; add 'orion'/'ews')
--   * locations         — EXISTING catalog; MERGE station rows for map layers

-- ---------------------------------------------------------------------------
-- Device catalog
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `climaticsystem.efc_clima.orion_devices` (
  source           STRING OPTIONS(description="Always 'orion'"),
  station_id       STRING OPTIONS(description="EWS#### from deviceName, else IMEI_*"),
  name             STRING,
  lat              FLOAT64,
  lon              FLOAT64,
  status           STRING OPTIONS(description="Online|Offline"),
  battery_pct      INT64,
  serial_number    STRING OPTIONS(description="IMEI"),
  reference        STRING,
  last_upload      TIMESTAMP,
  date_installed   TIMESTAMP,
  orion_device_id  INT64,
  pulled_at        TIMESTAMP
)
OPTIONS(description="Orion Data device catalog (EWS Switch MKII etc.)");

-- ---------------------------------------------------------------------------
-- Sensor / soft-sensor dictionary
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `climaticsystem.efc_clima.orion_sensors` (
  orion_device_id  INT64,
  station_id       STRING,
  kind             STRING OPTIONS(description="raw|soft"),
  sensor_id        INT64,
  soft_sensor_id   INT64,
  sensor_type      STRING,
  uom              STRING,
  channel_number   INT64,
  custom_name      STRING,
  scada_tag        STRING,
  mapped_variable  STRING OPTIONS(description="clima_series.variable if mapped"),
  pulled_at        TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Staging readings (full fidelity; optional)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `climaticsystem.efc_clima.orion_readings_staging` (
  station_id       STRING,
  orion_device_id  INT64,
  sensor_id        INT64,
  soft_sensor_id   INT64,
  sensor_type      STRING,
  uom              STRING,
  kind             STRING,
  reading_date     TIMESTAMP OPTIONS(description="Device local standard time (Orion)"),
  sensor_value     FLOAT64,
  quality_approval STRING,
  quality_code     STRING,
  comment          STRING,
  ingested_at      TIMESTAMP
)
PARTITION BY DATE(reading_date)
CLUSTER BY station_id, sensor_type;

-- ---------------------------------------------------------------------------
-- clima_series append contract (table already exists — document columns used)
-- ---------------------------------------------------------------------------
-- Expected long schema (align with existing ana/estacoes loaders):
--   source     STRING   -- 'orion'  (monitor may also accept alias 'ews')
--   station_id STRING   -- e.g. EWS6536
--   variable   STRING   -- nivel_m | precip_mm | vazao_m3s | temp_c | …
--   value      FLOAT64
--   obs_time   TIMESTAMP
-- Optional extras already seen in ops notes: date, precip_mm wide forms, etc.
-- If the live table uses `date` instead of `obs_time`, adapt the loader.

-- Example MERGE into locations (map layer):
-- MERGE `climaticsystem.efc_clima.locations` T
-- USING (
--   SELECT source, station_id, name, lat, lon
--   FROM `climaticsystem.efc_clima.orion_devices`
-- ) S
-- ON T.source = S.source AND T.station_id = S.station_id
-- WHEN MATCHED THEN UPDATE SET name=S.name, lat=S.lat, lon=S.lon
-- WHEN NOT MATCHED THEN INSERT (source, station_id, name, lat, lon)
--   VALUES (S.source, S.station_id, S.name, S.lat, S.lon);

-- Secret (do NOT store in git):
--   gcloud secrets create orion-user-key --project=climaticsystem
--   echo -n 'USER_KEY' | gcloud secrets versions add orion-user-key --data-file=-
-- Cloud Run / Cloud Build: ORION_USER_KEY=secrets:orion-user-key
