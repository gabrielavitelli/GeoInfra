-- Orion / EWS landing for climaticsystem.efc_clima
-- Live clima_series columns (do not invent others on append):
--   date, location_id, source, ingested_at, variable, value
-- location_id convention: ews_EWS6536  (cf. estacao_A044 for BNDMET/ANA)

CREATE TABLE IF NOT EXISTS `climaticsystem.efc_clima.orion_devices` (
  source           STRING,
  location_id      STRING,
  station_id       STRING,
  name             STRING,
  lat              FLOAT64,
  lon              FLOAT64,
  status           STRING,
  battery_pct      INT64,
  serial_number    STRING,
  reference        STRING,
  last_upload      TIMESTAMP,
  date_installed   TIMESTAMP,
  orion_device_id  INT64,
  pulled_at        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `climaticsystem.efc_clima.orion_sensors` (
  orion_device_id  INT64,
  location_id      STRING,
  station_id       STRING,
  kind             STRING,
  sensor_id        INT64,
  soft_sensor_id   INT64,
  sensor_type      STRING,
  uom              STRING,
  channel_number   INT64,
  custom_name      STRING,
  scada_tag        STRING,
  mapped_variable  STRING,
  pulled_at        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `climaticsystem.efc_clima.orion_readings_staging` (
  location_id      STRING,
  station_id       STRING,
  orion_device_id  INT64,
  sensor_id        INT64,
  soft_sensor_id   INT64,
  sensor_type      STRING,
  uom              STRING,
  kind             STRING,
  reading_date     TIMESTAMP,
  sensor_value     FLOAT64,
  quality_approval STRING,
  quality_code     STRING,
  comment          STRING,
  ingested_at      TIMESTAMP
)
PARTITION BY DATE(reading_date)
CLUSTER BY location_id, sensor_type;

-- Optional: expose Orion devices on the monitor map catalog
-- MERGE `climaticsystem.efc_clima.locations` T
-- USING (
--   SELECT source, location_id AS station_id, name, lat, lon
--   FROM `climaticsystem.efc_clima.orion_devices`
-- ) S
-- ON T.source = S.source AND T.station_id = S.station_id
-- WHEN MATCHED THEN UPDATE SET name = S.name, lat = S.lat, lon = S.lon
-- WHEN NOT MATCHED THEN INSERT ROW;
