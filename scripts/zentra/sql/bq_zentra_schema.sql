-- ZENTRA Cloud landing for climaticsystem.efc_clima
-- Live clima_series columns (do not invent others on append):
--   date, location_id, source, ingested_at, variable, value
-- location_id convention: zentra_z6_35936
-- source: zentra

CREATE TABLE IF NOT EXISTS `climaticsystem.efc_clima.zentra_devices` (
  source              STRING,
  location_id         STRING,
  station_id          STRING,
  name                STRING,
  lat                 FLOAT64,
  lon                 FLOAT64,
  status              STRING,
  model               STRING,
  device_type         INT64,
  organization_name   STRING,
  n_ports             INT64,
  sensors             ARRAY<STRING>,
  last_measurement    INT64,
  asset_class         STRING,
  zentra_device_id    STRING,
  pulled_at           TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `climaticsystem.efc_clima.zentra_readings_staging` (
  device_id           STRING,
  station_id          STRING,
  location_id         STRING,
  port_num            INT64,
  sensor_name         STRING,
  measurement         STRING,
  unit                STRING,
  value               FLOAT64,
  datetime            STRING,
  timestamp           INT64,
  reading_id          INT64,
  error_code          INT64,
  position            FLOAT64,
  mapped_variable     STRING,
  ingested_at         TIMESTAMP
)
PARTITION BY DATE(TIMESTAMP_SECONDS(timestamp))
CLUSTER BY location_id, measurement;

-- Optional map catalog MERGE (follow-up — map UI wiring):
-- MERGE `climaticsystem.efc_clima.locations` T
-- USING (
--   SELECT source, location_id AS station_id, name, lat, lon
--   FROM `climaticsystem.efc_clima.zentra_devices`
--   WHERE status = 'active'
-- ) S
-- ON T.source = S.source AND T.station_id = S.station_id
-- WHEN MATCHED THEN UPDATE SET name = S.name, lat = S.lat, lon = S.lon
-- WHEN NOT MATCHED THEN INSERT ROW;
