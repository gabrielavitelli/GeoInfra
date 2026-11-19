"""Zentra (TEROS/ATMOS) catalog + last readings for map / Sensores aba."""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_TZ_SP = ZoneInfo("America/Sao_Paulo")

# Sentinel / bad water-potential readings from ZENTRA when sensor dry/offline.
_WP_SENTINEL = -90000.0

# GCS snapshots written by the Zentra ingest job (local/ADC-friendly fallback).
_DEFAULT_DEVICES_GCS = "gs://efc-clima-data/obs/zentra/zentra_devices_latest.json"
_DEFAULT_LOCATIONS_GCS = "gs://efc-clima-data/obs/zentra/zentra_locations_latest.json"
_DEFAULT_SERIES_GCS = "gs://efc-clima-data/obs/zentra/zentra_clima_series_latest.ndjson"

# Series shown in map popup (compact) and sensors table — all mapped env vars.
POPUP_VARS: tuple[str, ...] = (
    "soil_vwc",
    "water_potential_kpa",
    "precip_mm",
    "soil_temp_c",
    "temp_c",
    "rh_pct",
    "pressure_hpa",
    "battery_pct",
    "signal_pct",
    "battery_v",
)

VAR_LABELS_PT: dict[str, str] = {
    "soil_vwc": "VWC: solo",
    "water_potential_kpa": "Pot. água",
    "precip_mm": "Precip",
    "battery_pct": "Bateria",
    "signal_pct": "Sinal",
    "battery_v": "Bateria V",
    "soil_temp_c": "Temp solo",
    "temp_c": "Temp ar",
    "rh_pct": "UR",
    "pressure_hpa": "Pressão",
}

VAR_UNITS: dict[str, str] = {
    "soil_vwc": "m³/m³",
    "water_potential_kpa": "kPa",
    "precip_mm": "mm",
    "battery_pct": "%",
    "signal_pct": "%",
    "battery_v": "V",
    "soil_temp_c": "°C",
    "temp_c": "°C",
    "rh_pct": "%",
    "pressure_hpa": "hPa",
}

STATUS_LABELS_PT: dict[str, str] = {
    "active": "ATIVO",
    "inactive": "INATIVO",
}

STATUS_LABELS_EN: dict[str, str] = {
    "active": "ACTIVE",
    "inactive": "INACTIVE",
}

# Card primary reading preference (soil → potential → precip).
PRIMARY_VARS: tuple[str, ...] = (
    "soil_vwc",
    "water_potential_kpa",
    "precip_mm",
    "soil_temp_c",
)


def _bq_dataset(dataset: str | None = None) -> str:
    return (dataset or os.environ.get("BQ_DATASET") or "efc_clima").strip() or "efc_clima"


def _gcs_fallback_enabled() -> bool:
    return (os.environ.get("ZENTRA_SKIP_GCS_FALLBACK") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def _client():
    try:
        from gcp.bq_load_clima import _bq_client

        return _bq_client()
    except Exception as exc:
        logger.warning("zentra BQ client unavailable: %s", exc)
        return None


def _pfx(dataset: str) -> str:
    from gcp.bq_config import get_bq_fully_qualified_prefix

    return get_bq_fully_qualified_prefix(dataset)


def _read_uri_bytes(uri: str) -> bytes | None:
    """Read ``gs://`` (ADC) or local ``file://`` / path bytes. Returns None on failure."""
    u = (uri or "").strip()
    if not u:
        return None
    try:
        if u.startswith("gs://"):
            from google.cloud import storage

            _, _, rest = u.partition("gs://")
            bucket_name, _, blob_name = rest.partition("/")
            if not bucket_name or not blob_name:
                return None
            client = storage.Client()
            return client.bucket(bucket_name).blob(blob_name).download_as_bytes()
        if u.startswith("file://"):
            u = u[7:]
        with open(u, "rb") as fh:
            return fh.read()
    except Exception as exc:
        logger.warning("zentra URI read failed (%s): %s", u, exc)
        return None


def _location_id_from_device_id(device_id: str) -> str:
    did = (device_id or "").strip()
    if not did:
        return ""
    if did.startswith("zentra_"):
        return did
    return "zentra_" + did.replace("-", "_")


def _asset_class_from_sensors(sensors: list[str]) -> str:
    names = " ".join(sensors).upper()
    has_teros = "TEROS" in names
    has_atmos = "ATMOS" in names
    if has_teros and has_atmos:
        return "mixed"
    if has_atmos:
        return "climate"
    if has_teros:
        return "geotech"
    return "—"


def _devices_df_from_raw_payload(payload: Any):
    """Normalize Zentra Cloud ``devices`` JSON → DataFrame matching BQ columns."""
    import pandas as pd

    if isinstance(payload, dict):
        devices = payload.get("devices") or []
    elif isinstance(payload, list):
        devices = payload
    else:
        return None
    rows: list[dict[str, Any]] = []
    for d in devices:
        if not isinstance(d, dict):
            continue
        loc_obj = d.get("location") or {}
        try:
            lat = float(loc_obj.get("latitude"))
            lon = float(loc_obj.get("longitude"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        if abs(lat) <= 0.1 or abs(lon) <= 0.1:
            continue
        device_id = str(d.get("device_id") or d.get("name") or "").strip()
        if not device_id:
            continue
        ports = d.get("ports") or []
        sensors: list[str] = []
        seen: set[str] = set()
        for p in ports:
            if not isinstance(p, dict):
                continue
            sn = str(p.get("sensor_short_name") or "").strip()
            if sn and sn not in seen:
                seen.add(sn)
                sensors.append(sn)
        data_range = d.get("data_range") or {}
        lm = data_range.get("last_measurement")
        try:
            lm_i = int(lm) if lm is not None and str(lm).strip() != "" else None
        except (TypeError, ValueError):
            lm_i = None
        # Raw Cloud dump has no status; treat readable + coords as active (matches BQ).
        can_read = d.get("can_read_data")
        status = "active" if can_read is not False else "inactive"
        if status != "active":
            continue
        loc_id = _location_id_from_device_id(device_id)
        name = str(d.get("name") or device_id).strip()
        rows.append(
            {
                "codEstacao": loc_id,
                "location_id": loc_id,
                "nome": name,
                "latitude": lat,
                "longitude": lon,
                "status": status,
                "asset_class": _asset_class_from_sensors(sensors),
                "model": str(d.get("model") or "").strip() or None,
                "sensors": sensors,
                "last_measurement": lm_i,
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows)


def _devices_df_from_locations_payload(payload: Any):
    """Normalize ingest ``zentra_locations_latest.json`` list → DataFrame."""
    import pandas as pd

    items = payload if isinstance(payload, list) else (payload.get("locations") if isinstance(payload, dict) else None)
    if not items:
        return None
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        status = str(extra.get("status") or item.get("status") or "active").strip() or "active"
        if status != "active":
            continue
        try:
            lat = float(item.get("lat"))
            lon = float(item.get("lon"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        if abs(lat) <= 0.1 or abs(lon) <= 0.1:
            continue
        loc_id = str(item.get("location_id") or "").strip()
        if not loc_id:
            station = str(item.get("station_id") or item.get("name") or "").strip()
            loc_id = _location_id_from_device_id(station)
        if not loc_id:
            continue
        sensors = extra.get("sensors") or item.get("sensors") or []
        if hasattr(sensors, "tolist"):
            sensors = list(sensors.tolist())
        if not isinstance(sensors, (list, tuple)):
            sensors = []
        sensors = [str(s) for s in sensors if s]
        lm = extra.get("last_measurement") or item.get("last_measurement")
        try:
            lm_i = int(lm) if lm is not None and str(lm).strip() != "" else None
        except (TypeError, ValueError):
            lm_i = None
        rows.append(
            {
                "codEstacao": loc_id,
                "location_id": loc_id,
                "nome": str(item.get("name") or loc_id).strip(),
                "latitude": lat,
                "longitude": lon,
                "status": status,
                "asset_class": str(extra.get("asset_class") or item.get("asset_class") or _asset_class_from_sensors(sensors)),
                "model": str(extra.get("model") or item.get("model") or "").strip() or None,
                "sensors": sensors,
                "last_measurement": lm_i,
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows)


def load_zentra_devices_from_gcs(*, uri: str | None = None):
    """Load active Zentra devices from GCS snapshot (or local path)."""
    if not _gcs_fallback_enabled() and uri is None:
        return None
    primary = (uri or os.environ.get("ZENTRA_DEVICES_GCS_URI") or _DEFAULT_DEVICES_GCS).strip()
    raw = _read_uri_bytes(primary)
    if raw:
        try:
            payload = json.loads(raw.decode("utf-8"))
            df = _devices_df_from_raw_payload(payload)
            if df is not None and not df.empty:
                logger.info("zentra devices loaded from GCS (%s): %s rows", primary, len(df))
                return df
            # locations-shaped JSON pointed at by override URI
            df = _devices_df_from_locations_payload(payload)
            if df is not None and not df.empty:
                logger.info("zentra devices loaded from GCS locations (%s): %s rows", primary, len(df))
                return df
        except Exception as exc:
            logger.warning("zentra GCS devices parse failed (%s): %s", primary, exc)
    # Second chance: locations snapshot (richer status/sensors)
    alt = (os.environ.get("ZENTRA_LOCATIONS_GCS_URI") or _DEFAULT_LOCATIONS_GCS).strip()
    if alt and alt != primary:
        raw2 = _read_uri_bytes(alt)
        if raw2:
            try:
                payload2 = json.loads(raw2.decode("utf-8"))
                df2 = _devices_df_from_locations_payload(payload2)
                if df2 is not None and not df2.empty:
                    logger.info("zentra devices loaded from GCS locations (%s): %s rows", alt, len(df2))
                    return df2
            except Exception as exc:
                logger.warning("zentra GCS locations parse failed (%s): %s", alt, exc)
    return None


def load_zentra_latest_from_gcs(
    *,
    uri: str | None = None,
    variables: tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    """Latest clima_series-like values from NDJSON snapshot (per location/variable)."""
    if not _gcs_fallback_enabled() and uri is None:
        return {}
    path = (uri or os.environ.get("ZENTRA_SERIES_GCS_URI") or _DEFAULT_SERIES_GCS).strip()
    raw = _read_uri_bytes(path)
    if not raw:
        return {}
    vars_ = set(variables or POPUP_VARS)
    out: dict[str, dict[str, Any]] = {}
    try:
        text = raw.decode("utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            var = str(row.get("variable") or "")
            if var not in vars_:
                continue
            loc = str(row.get("location_id") or "").strip()
            if not loc:
                continue
            date = row.get("date") or row.get("ingested_at")
            try:
                val = float(row["value"]) if row.get("value") is not None else None
            except (TypeError, ValueError):
                val = None
            bucket = out.setdefault(loc, {})
            prev = bucket.get(var)
            # Keep the chronologically latest date when multiple lines exist.
            if prev is None or (date is not None and str(date) >= str(prev.get("date") or "")):
                bucket[var] = {"value": val, "date": date}
        return out
    except Exception as exc:
        logger.warning("zentra GCS series parse failed (%s): %s", path, exc)
        return {}


def _fmt_value(variable: str, value: float | None) -> str | None:
    if value is None or not math.isfinite(float(value)):
        return None
    v = float(value)
    if variable == "water_potential_kpa" and v <= _WP_SENTINEL:
        return None
    if variable in ("soil_vwc",):
        return f"{v:.3f}"
    if variable in ("water_potential_kpa", "precip_mm", "soil_temp_c"):
        return f"{v:.1f}"
    if variable in ("battery_pct", "signal_pct"):
        return f"{v:.0f}"
    return f"{v:.2f}"


def _freshness_label(ts: datetime | None, *, lang_pt: bool = True) -> str:
    """Relative age in America/Sao_Paulo wall-clock (há Xm / há Xh / há Xd)."""
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        # Naive timestamps from Zentra Cloud are local Brazil time.
        ts = ts.replace(tzinfo=_TZ_SP)
    now = datetime.now(_TZ_SP)
    age_s = (now - ts.astimezone(_TZ_SP)).total_seconds()
    if age_s < 0:
        age_s = 0.0
    age_m = age_s / 60.0
    age_h = age_s / 3600.0
    if age_m < 60:
        n = max(1, int(round(age_m))) if age_m >= 1 else 0
        if n <= 0:
            return "agora" if lang_pt else "now"
        return (f"há {n} min" if lang_pt else f"{n}m ago")
    if age_h < 24:
        n = max(1, int(round(age_h)))
        return (f"há {n} h" if lang_pt else f"{n}h ago")
    n = max(1, int(round(age_h / 24.0)))
    return (f"há {n} d" if lang_pt else f"{n}d ago")


def load_zentra_devices_df(*, bq_dataset: str | None = None):
    """Active Zentra loggers with valid coords (DataFrame for Folium / 3D).

    Prefers BigQuery ``zentra_devices``; on failure/empty falls back to GCS
    ``gs://efc-clima-data/obs/zentra/zentra_devices_latest.json`` (local-friendly).
    """
    import pandas as pd

    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is not None:
        try:
            pfx = _pfx(ds)
            sql = f"""
                SELECT
                  location_id AS codEstacao,
                  location_id,
                  name AS nome,
                  lat AS latitude,
                  lon AS longitude,
                  status,
                  asset_class,
                  model,
                  sensors,
                  last_measurement
                FROM `{pfx}.zentra_devices`
                WHERE status = 'active'
                  AND lat IS NOT NULL AND lon IS NOT NULL
                  AND ABS(lat) > 0.1 AND ABS(lon) > 0.1
            """
            df = client.query(sql).to_dataframe()
            if df is not None and not df.empty:
                df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
                df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
                df = df.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
                if not df.empty:
                    return df
            logger.warning("zentra BQ devices empty (%s.zentra_devices); trying GCS fallback", pfx)
        except Exception as exc:
            logger.warning("zentra BQ devices query failed: %s; trying GCS fallback", exc)
    else:
        logger.warning("zentra BQ client missing; trying GCS fallback for devices")
    return load_zentra_devices_from_gcs()


def load_zentra_latest_by_location(
    *,
    bq_dataset: str | None = None,
    variables: tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    """location_id → {variable → {value, date}} for latest clima_series rows."""
    ds = _bq_dataset(bq_dataset)
    client = _client()
    vars_ = variables or POPUP_VARS
    if client is not None:
        try:
            pfx = _pfx(ds)
            var_list = ", ".join(f"'{v}'" for v in vars_)
            sql = f"""
                WITH ranked AS (
                  SELECT
                    location_id,
                    variable,
                    value,
                    date,
                    ROW_NUMBER() OVER (
                      PARTITION BY location_id, variable
                      ORDER BY date DESC
                    ) AS rn
                  FROM `{pfx}.clima_series`
                  WHERE source = 'zentra'
                    AND variable IN ({var_list})
                )
                SELECT location_id, variable, value, date
                FROM ranked
                WHERE rn = 1
            """
            rows = list(client.query(sql).result())
            out: dict[str, dict[str, Any]] = {}
            for r in rows:
                loc = str(r["location_id"])
                var = str(r["variable"])
                try:
                    val = float(r["value"]) if r["value"] is not None else None
                except (TypeError, ValueError):
                    val = None
                bucket = out.setdefault(loc, {})
                bucket[var] = {"value": val, "date": r["date"]}
            if out:
                return out
            logger.warning("zentra BQ latest empty; trying GCS series fallback")
        except Exception as exc:
            logger.warning("zentra BQ latest query failed: %s; trying GCS series fallback", exc)
    else:
        logger.warning("zentra BQ client missing; trying GCS series fallback")
    return load_zentra_latest_from_gcs(variables=vars_)


def _nearest_km(lat: float, lon: float, main_line, cum_km) -> float | None:
    if main_line is None or cum_km is None or not len(main_line):
        return None
    best_i = 0
    best_d = None
    for i, pt in enumerate(main_line):
        try:
            d = (float(pt[0]) - lat) ** 2 + (float(pt[1]) - lon) ** 2
        except (TypeError, ValueError, IndexError):
            continue
        if best_d is None or d < best_d:
            best_d = d
            best_i = i
    if best_d is None:
        return None
    try:
        return float(cum_km[best_i])
    except Exception:
        return None


def build_zentra_sensor_rows(
    *,
    bq_dataset: str | None = None,
    lang_pt: bool = True,
    main_line=None,
    cum_km=None,
    corridor_only: bool = False,
) -> list[dict[str, Any]]:
    """Rows for Sensores aba + map popups (inspection-friendly)."""
    df = load_zentra_devices_df(bq_dataset=bq_dataset)
    if df is None or getattr(df, "empty", True):
        return []
    latest = load_zentra_latest_by_location(bq_dataset=bq_dataset)
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        try:
            lat = float(r["latitude"])
            lon = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if corridor_only and not (-8.0 <= lat <= -2.0 and -50.0 <= lon <= -44.0):
            continue
        loc = str(r.get("location_id") or r.get("codEstacao") or "").strip()
        name = str(r.get("nome") or loc).strip()
        sensors = r.get("sensors")
        if hasattr(sensors, "tolist"):
            sensors = list(sensors.tolist())
        elif not isinstance(sensors, (list, tuple)):
            sensors = []
        sensors = [str(s) for s in sensors if s]
        last = latest.get(loc) or {}
        series: list[dict[str, Any]] = []
        last_dates = []
        for var in POPUP_VARS:
            cell = last.get(var)
            if not cell:
                continue
            txt = _fmt_value(var, cell.get("value"))
            if txt is None:
                continue
            unit = VAR_UNITS.get(var, "")
            series.append(
                {
                    "variable": var,
                    "label": VAR_LABELS_PT.get(var, var),
                    "value": cell.get("value"),
                    "value_txt": txt,
                    "unit": unit,
                    "date": cell.get("date"),
                }
            )
            if cell.get("date") is not None:
                last_dates.append(cell["date"])
        # Prefer device last_measurement (unix) over date-only series cells —
        # date-at-midnight UTC falsely ages readings by ~12–18h in Brazil.
        lm = r.get("last_measurement")
        try:
            lm_i = int(lm) if lm is not None and str(lm).strip() != "" else None
        except (TypeError, ValueError):
            lm_i = None
        last_ts: datetime | None = None
        if lm_i:
            last_ts = datetime.fromtimestamp(lm_i, tz=timezone.utc)
        elif last_dates:
            try:
                cand = max(last_dates)
            except Exception:
                cand = last_dates[0]
            if isinstance(cand, datetime):
                last_ts = cand
            elif cand is not None:
                try:
                    raw = str(cand)
                    # Date-only → assume end-of-day São Paulo (not midnight UTC).
                    if len(raw) <= 10:
                        last_ts = datetime.fromisoformat(raw[:10] + "T23:59:00").replace(
                            tzinfo=_TZ_SP
                        )
                    else:
                        last_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except Exception:
                    last_ts = None
        km = _nearest_km(lat, lon, main_line, cum_km)
        status = str(r.get("status") or "").strip() or "—"
        status_key = status.lower()
        status_map = STATUS_LABELS_PT if lang_pt else STATUS_LABELS_EN
        status_label = status_map.get(status_key, status.upper() if status != "—" else "—")
        asset = str(r.get("asset_class") or "").strip() or "—"
        # Signal / cobertura for card metric (prefer real signal %).
        sig = next((s for s in series if s["variable"] == "signal_pct"), None)
        bat = next((s for s in series if s["variable"] == "battery_pct"), None)
        cobertura_txt = f"{sig['value_txt']}%" if sig else "—"
        primary = next(
            (s for v in PRIMARY_VARS for s in series if s["variable"] == v),
            None,
        )
        # Location line: Trecho km N – sensor types (photo-style).
        sensors_short = " · ".join(sensors[:3]) if sensors else ""
        if km is not None and sensors_short:
            location_line = f"Trecho km {km:.0f} – {sensors_short}"
        elif km is not None:
            location_line = f"Trecho km {km:.0f}"
        elif sensors_short:
            location_line = sensors_short
        else:
            location_line = loc
        health_bits = []
        if bat:
            health_bits.append(f"bat {bat['value_txt']}%")
        if sig:
            health_bits.append(f"sinal {sig['value_txt']}%")
        rows.append(
            {
                "location_id": loc,
                "name": name,
                "lat": lat,
                "lon": lon,
                "km": round(km, 1) if km is not None else None,
                "status": status,
                "status_label": status_label,
                "asset_class": asset,
                "sensors": sensors,
                "series": series,
                "primary": primary,
                "cobertura": cobertura_txt,
                "sinal": cobertura_txt,
                "location_line": location_line,
                "health": " · ".join(health_bits) if health_bits else "—",
                "last_ts": last_ts.isoformat() if isinstance(last_ts, datetime) else None,
                "freshness": _freshness_label(last_ts, lang_pt=lang_pt),
                "map_url_qs": f"focus=zentra&lat={lat:.5f}&lon={lon:.5f}&zid={loc}",
                "port_children": [],
                "avg_port_count": 0,
            }
        )
    rows.sort(key=lambda x: (x.get("km") is None, x.get("km") or 0, x["name"]))
    enrich_sensor_rows_with_port_averages(rows, lang_pt=lang_pt)
    return rows


def _haversine_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Equirectangular distance in metres — fine for ~100–150 m clusters near equator."""
    lat1, lon1 = math.radians(float(a["lat"])), math.radians(float(a["lon"]))
    lat2, lon2 = math.radians(float(b["lat"])), math.radians(float(b["lon"]))
    x = (lon2 - lon1) * math.cos((lat1 + lat2) / 2.0)
    y = lat2 - lat1
    return math.sqrt(x * x + y * y) * 6371000.0


def _cluster_logger_groups(
    rows: list[dict[str, Any]],
    *,
    cluster_m: float = 120.0,
) -> list[list[dict[str, Any]]]:
    """Group nearby loggers (greedy seed clusters within cluster_m)."""
    pending = [
        r
        for r in (rows or [])
        if r.get("lat") is not None
        and r.get("lon") is not None
        and abs(float(r["lat"])) > 0.1
        and abs(float(r["lon"])) > 0.1
    ]
    used: set[int] = set()
    groups: list[list[dict[str, Any]]] = []
    for i, seed in enumerate(pending):
        if i in used:
            continue
        cluster = [seed]
        used.add(i)
        for j, other in enumerate(pending):
            if j in used:
                continue
            if _haversine_m(seed, other) <= cluster_m:
                cluster.append(other)
                used.add(j)
        groups.append(cluster)
    # Orphans without valid coords stay as singleton cards.
    for r in rows or []:
        if r in pending:
            continue
        groups.append([r])
    return groups


def cluster_sensor_rows_into_sites(
    rows: list[dict[str, Any]],
    *,
    lang_pt: bool = True,
    cluster_m: float = 120.0,
) -> list[dict[str, Any]]:
    """One Sensores card per geographic ponto/talude (~100–150 m).

    Collapsed card shows site-mean VWC; expand reveals each ZL6 logger and its
    ports/depths. Same clustering radius as Taludes Monitorados.
    """
    sites: list[dict[str, Any]] = []
    for gi, cluster in enumerate(_cluster_logger_groups(rows, cluster_m=cluster_m)):
        # Stable order inside site: km then name.
        cluster = sorted(
            cluster,
            key=lambda c: (c.get("km") is None, c.get("km") or 0, str(c.get("name") or "")),
        )
        n = len(cluster)
        lats = [float(c["lat"]) for c in cluster if c.get("lat") is not None]
        lons = [float(c["lon"]) for c in cluster if c.get("lon") is not None]
        kms = [float(c["km"]) for c in cluster if c.get("km") is not None]
        lat = sum(lats) / len(lats) if lats else float(cluster[0].get("lat") or 0)
        lon = sum(lons) / len(lons) if lons else float(cluster[0].get("lon") or 0)
        km = round(sum(kms) / len(kms), 1) if kms else None
        loc_ids = [str(c.get("location_id") or "") for c in cluster]
        site_id = f"site-{km if km is not None else gi}-{loc_ids[0] if loc_ids else gi}"

        # Site mean VWC = mean of each logger's VWC (prefer logger primary when
        # already averaged across ports; else raw soil_vwc primary).
        vwc_vals: list[float] = []
        for c in cluster:
            prim = c.get("primary") or {}
            if prim.get("variable") == "soil_vwc" and prim.get("value") is not None:
                try:
                    vwc_vals.append(float(prim["value"]))
                except (TypeError, ValueError):
                    pass
        primary = None
        if n == 1 and cluster[0].get("primary"):
            # Keep the logger's own primary (port average / raw) on singleton cards.
            primary = dict(cluster[0]["primary"])
        elif vwc_vals:
            avg = sum(vwc_vals) / len(vwc_vals)
            txt = _fmt_value("soil_vwc", avg)
            if txt is not None:
                primary = {
                    "variable": "soil_vwc",
                    "label": ("VWC médio do ponto" if lang_pt else "Site mean VWC"),
                    "value": avg,
                    "value_txt": txt,
                    "unit": VAR_UNITS.get("soil_vwc", ""),
                    "date": None,
                    "is_average": True,
                    "n_ports": sum(int(c.get("avg_port_count") or 0) for c in cluster)
                    or len(vwc_vals),
                    "n_loggers": n,
                }

        sensors: list[str] = []
        seen_s: set[str] = set()
        for c in cluster:
            for s in c.get("sensors") or []:
                ss = str(s)
                if ss and ss not in seen_s:
                    seen_s.add(ss)
                    sensors.append(ss)

        statuses = [str(c.get("status") or "").lower() for c in cluster]
        if any(s == "active" for s in statuses):
            status = "active"
        elif statuses and all(s == "inactive" for s in statuses if s):
            status = "inactive"
        else:
            status = str(cluster[0].get("status") or "—")
        status_map = STATUS_LABELS_PT if lang_pt else STATUS_LABELS_EN
        status_label = status_map.get(status, str(cluster[0].get("status_label") or status))

        fresh_rank = {"agora": 0, "now": 0, "hoje": 1, "today": 1}
        freshness = "—"
        best_f = 99
        for c in cluster:
            f = str(c.get("freshness") or "—")
            key = f.lower().split()[0] if f else "—"
            rank = fresh_rank.get(key, 50)
            if rank < best_f:
                best_f = rank
                freshness = f

        # Signal: best available among loggers.
        sinal = "—"
        for c in cluster:
            s = c.get("sinal") or c.get("cobertura")
            if s and s != "—":
                sinal = s
                break

        if lang_pt:
            if km is not None:
                name = f"Talude km {km:.0f}"
                if n > 1:
                    name += f" · {n} loggers"
            else:
                name = (
                    f"Ponto Zentra · {n} loggers"
                    if n > 1
                    else str(cluster[0].get("name") or "Zentra")
                )
            if n > 1 and km is not None:
                location_line = f"Trecho km {km:.0f} – {n} ZL6"
            elif n > 1:
                location_line = f"{n} loggers Zentra"
            else:
                location_line = str(cluster[0].get("location_line") or "")
        else:
            if km is not None:
                name = f"Slope km {km:.0f}"
                if n > 1:
                    name += f" · {n} loggers"
            else:
                name = (
                    f"Zentra site · {n} loggers"
                    if n > 1
                    else str(cluster[0].get("name") or "Zentra")
                )
            if n > 1 and km is not None:
                location_line = f"Segment km {km:.0f} – {n} ZL6"
            elif n > 1:
                location_line = f"{n} Zentra loggers"
            else:
                location_line = str(cluster[0].get("location_line") or "")

        # Multi-logger: nest ports under each logger; single logger keeps flat ports.
        loggers: list[dict[str, Any]] = []
        for c in cluster:
            loggers.append(
                {
                    "location_id": c.get("location_id"),
                    "name": c.get("name"),
                    "lat": c.get("lat"),
                    "lon": c.get("lon"),
                    "km": c.get("km"),
                    "status": c.get("status"),
                    "status_label": c.get("status_label"),
                    "sensors": c.get("sensors") or [],
                    "primary": c.get("primary"),
                    "freshness": c.get("freshness"),
                    "sinal": c.get("sinal") or c.get("cobertura"),
                    "port_children": c.get("port_children") or [],
                    "avg_port_count": c.get("avg_port_count") or 0,
                    "asset_class": c.get("asset_class"),
                    "location_line": c.get("location_line"),
                }
            )

        port_children = cluster[0].get("port_children") or [] if n == 1 else []
        sites.append(
            {
                "location_id": site_id if n > 1 else loc_ids[0],
                "site_id": site_id,
                "is_site": True,
                "n_loggers": n,
                "loggers": loggers,
                "location_ids": loc_ids,
                "name": name,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "km": km,
                "status": status,
                "status_label": status_label,
                "asset_class": (
                    "mixed"
                    if any(str(c.get("asset_class") or "") == "mixed" for c in cluster)
                    else (cluster[0].get("asset_class") or "—")
                ),
                "sensors": sensors,
                "series": cluster[0].get("series") or [],
                "primary": primary,
                "cobertura": sinal,
                "sinal": sinal,
                "location_line": location_line,
                "health": cluster[0].get("health") or "—",
                "last_ts": cluster[0].get("last_ts"),
                "freshness": freshness,
                "map_url_qs": f"focus=zentra&lat={lat:.5f}&lon={lon:.5f}&zid={loc_ids[0] if loc_ids else ''}",
                "port_children": port_children,
                "avg_port_count": sum(int(c.get("avg_port_count") or 0) for c in cluster),
            }
        )
    sites.sort(key=lambda x: (x.get("km") is None, x.get("km") or 0, x.get("name") or ""))
    return sites


def enrich_sensor_rows_with_port_averages(
    rows: list[dict[str, Any]],
    *,
    lang_pt: bool = True,
) -> list[dict[str, Any]]:
    """Attach per-port children + replace primary with mean VWC across depths/ports.

    Collapsed Sensores cards show the ponto average; expand reveals each TEROS
    port (depth) as a child row with its own Ver detalhes target.
    """
    if not rows:
        return rows
    try:
        from monitor_web.services.sensor_series import latest_port_readings_by_location
    except Exception as exc:
        logger.warning("port average enrichment unavailable: %s", exc)
        return rows
    try:
        by_loc = latest_port_readings_by_location(
            variables=("soil_vwc", "water_potential_kpa")
        )
    except Exception as exc:
        logger.warning("latest_port_readings_by_location failed: %s", exc)
        return rows
    for row in rows:
        loc = str(row.get("location_id") or "")
        readings = by_loc.get(loc) or []
        children: list[dict[str, Any]] = []
        vwc_vals: list[float] = []
        for r in readings:
            var = str(r.get("variable") or "")
            val = r.get("value")
            try:
                fv = float(val) if val is not None else None
            except (TypeError, ValueError):
                fv = None
            if fv is None:
                continue
            txt = _fmt_value(var, fv)
            if txt is None:
                continue
            port = r.get("port")
            sensor = str(r.get("sensor") or "").strip() or "—"
            if lang_pt:
                label = f"Porta {port} · {sensor}" if port is not None else sensor
            else:
                label = f"Port {port} · {sensor}" if port is not None else sensor
            children.append(
                {
                    "port": port,
                    "sensor": sensor,
                    "variable": var,
                    "label": label,
                    "var_label": VAR_LABELS_PT.get(var, var),
                    "value": fv,
                    "value_txt": txt,
                    "unit": VAR_UNITS.get(var, ""),
                    "datetime": r.get("datetime"),
                }
            )
            if var == "soil_vwc":
                vwc_vals.append(fv)
        row["port_children"] = children
        row["avg_port_count"] = len(vwc_vals)
        if len(vwc_vals) >= 2:
            avg = sum(vwc_vals) / len(vwc_vals)
            txt = _fmt_value("soil_vwc", avg)
            if txt is not None:
                row["primary"] = {
                    "variable": "soil_vwc",
                    "label": ("VWC médio" if lang_pt else "Mean VWC"),
                    "value": avg,
                    "value_txt": txt,
                    "unit": VAR_UNITS.get("soil_vwc", ""),
                    "date": None,
                    "is_average": True,
                    "n_ports": len(vwc_vals),
                }
        elif len(vwc_vals) == 1 and not row.get("primary"):
            txt = _fmt_value("soil_vwc", vwc_vals[0])
            if txt is not None:
                row["primary"] = {
                    "variable": "soil_vwc",
                    "label": VAR_LABELS_PT.get("soil_vwc", "VWC"),
                    "value": vwc_vals[0],
                    "value_txt": txt,
                    "unit": VAR_UNITS.get("soil_vwc", ""),
                    "date": None,
                    "is_average": False,
                    "n_ports": 1,
                }
    return rows


def build_taludes_monitorados(
    rows: list[dict[str, Any]] | None = None,
    *,
    lang_pt: bool = True,
    main_line=None,
    cum_km=None,
    cluster_m: float = 120.0,
) -> list[dict[str, Any]]:
    """Chooser sites for Visão Geral → Taludes Monitorados (fly-to).

    Clusters nearby Zentra loggers into one talude/ponto site.
    Tunnel / rock-cut sites live in :func:`build_tuneis_monitorados`.
    """
    if rows is None:
        rows = build_zentra_sensor_rows(
            lang_pt=lang_pt, main_line=main_line, cum_km=cum_km, corridor_only=True
        )
    sites: list[dict[str, Any]] = []
    for i, cluster in enumerate(_cluster_logger_groups(rows or [], cluster_m=cluster_m)):
        # Skip coord-less orphans for the map chooser (cards still show them).
        if not any(
            c.get("lat") is not None
            and c.get("lon") is not None
            and abs(float(c["lat"])) > 0.1
            and abs(float(c["lon"])) > 0.1
            for c in cluster
        ):
            continue
        lats = [float(c["lat"]) for c in cluster if c.get("lat") is not None]
        lons = [float(c["lon"]) for c in cluster if c.get("lon") is not None]
        kms = [float(c["km"]) for c in cluster if c.get("km") is not None]
        lat = sum(lats) / len(lats)
        lon = sum(lons) / len(lons)
        km = round(sum(kms) / len(kms), 1) if kms else None
        n = len(cluster)
        if lang_pt:
            if km is not None:
                label = f"Talude km {km:.0f}"
                if n > 1:
                    label += f" · {n} sensores"
            else:
                label = f"Ponto Zentra ({n})" if n > 1 else f"Zentra {cluster[0].get('name')}"
        else:
            if km is not None:
                label = f"Slope km {km:.0f}"
                if n > 1:
                    label += f" · {n} sensors"
            else:
                label = f"Zentra site ({n})" if n > 1 else f"Zentra {cluster[0].get('name')}"
        sites.append(
            {
                "id": f"zentra-cluster-{km if km is not None else i}",
                "kind": "talude",
                "label": label,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "km": km,
                "zoom": 15.8,
                "n_sensors": n,
                "location_ids": [str(c.get("location_id") or "") for c in cluster],
            }
        )
    sites.sort(key=lambda s: (s.get("km") is None, s.get("km") or 0, s.get("label") or ""))
    return sites


def build_tuneis_monitorados(
    *,
    lang_pt: bool = True,
    main_line=None,
    cum_km=None,
) -> list[dict[str, Any]]:
    """Chooser sites for Visão Geral → Túnel (cortes rochosos / túneis).

    Midpoints from ``map_3d_overlay._TUNNEL_KM_RANGES`` (km 372.56–373.535
    and 374.085–375.2).
    """
    sites: list[dict[str, Any]] = []
    try:
        from monitor_web.services.map_3d_overlay import _TUNNEL_KM_RANGES
        from lib_efc import position_at_km

        if main_line is None or cum_km is None:
            return sites
        for km0, km1, tunnel_label in _TUNNEL_KM_RANGES:
            mid = (float(km0) + float(km1)) / 2.0
            pos = position_at_km(mid, main_line, cum_km)
            if pos is None:
                continue
            if lang_pt:
                tlabel = f"{tunnel_label} km {km0:.0f}–{km1:.0f}"
            else:
                tlabel = f"Rock cut km {km0:.0f}–{km1:.0f}"
            sites.append(
                {
                    "id": f"tunnel-{km0:.0f}-{km1:.0f}",
                    "kind": "tunnel",
                    "label": tlabel,
                    "lat": round(float(pos[0]), 6),
                    "lon": round(float(pos[1]), 6),
                    "km": round(mid, 1),
                    "zoom": 14.5,
                    "n_sensors": 0,
                    "location_ids": [],
                }
            )
    except Exception as exc:
        logger.warning("tuneis monitorados skipped: %s", exc)
    sites.sort(key=lambda s: (s.get("km") is None, s.get("km") or 0, s.get("label") or ""))
    return sites


def zentra_popup_html(row: dict[str, Any], *, lang_pt: bool = True) -> str:
    """Compact Folium popup: series + last value + freshness + open CTA."""
    title = row.get("name") or row.get("location_id") or "Zentra"
    loc = row.get("location_id") or ""
    km = row.get("km")
    head = f"<b>Zentra {title}</b>"
    if km is not None:
        head += f"<br>km {km:.0f}"
    elif loc:
        head += f"<br><span style='color:#94a3b8'>{loc}</span>"
    lines = [head]
    for s in row.get("series") or []:
        unit = s.get("unit") or ""
        lines.append(
            f"{s.get('label')}: <b>{s.get('value_txt')}</b>"
            + (f" {unit}" if unit else "")
        )
    fresh = row.get("freshness") or "—"
    lines.append(
        f"<span style='color:#94a3b8'>"
        + ("atualizado " if lang_pt else "updated ")
        + f"{fresh}</span>"
    )
    sensors = row.get("sensors") or []
    if sensors:
        lines.append(
            "<span style='color:#64748b'>"
            + " · ".join(sensors[:4])
            + "</span>"
        )
    # CTA: click opens series modal via parent/top postMessage (Folium nested iframes).
    cta = "Ver séries" if lang_pt else "View series"
    lines.append(
        "<div style='margin-top:8px'>"
        f"<button type='button' class='efc-zentra-open-series' "
        f"data-location-id='{loc}' "
        "style='cursor:pointer;border:0;border-radius:6px;padding:6px 10px;"
        "background:#f97316;color:#fff;"
        "font:600 12px/1.2 IBM Plex Sans,Segoe UI,sans-serif'>"
        f"{cta}</button></div>"
    )
    return "<br>".join(lines)


def add_zentra_markers_to_map(m, rows: list[dict[str, Any]], *, lang_pt: bool = True):
    """Distinct coral circles on a dedicated FeatureGroup (toggleable)."""
    import json

    import folium
    from jinja2 import Template

    if not m or not rows:
        return None
    fg = folium.FeatureGroup(name="Zentra", show=True, control=False)
    color = "#f97316"  # coral/orange — distinct from BNDMET green / ANA blue / EWS amber fill
    click_payloads: list[tuple[str, str]] = []
    for row in rows:
        lat, lon = row.get("lat"), row.get("lon")
        if lat is None or lon is None:
            continue
        body = zentra_popup_html(row, lang_pt=lang_pt)
        tip = f"Zentra — {row.get('name') or row.get('location_id')}"
        loc = str(row.get("location_id") or "")
        name = str(row.get("name") or loc)
        km = row.get("km")
        try:
            km_f = float(km) if km is not None else None
        except (TypeError, ValueError):
            km_f = None
        marker = folium.CircleMarker(
            location=[float(lat), float(lon)],
            radius=7,
            color="#9a3412",
            fill=True,
            fillColor=color,
            fillOpacity=0.92,
            weight=2.0,
            popup=folium.Popup(body, max_width=240),
            tooltip=tip,
        )
        marker.add_to(fg)
        payload = {
            "type": "efc-sensor-open",
            "source": "zentra",
            "location_id": loc,
            "name": name,
            "lat": float(lat),
            "lon": float(lon),
            "km": km_f,
        }
        click_payloads.append((marker.get_name(), json.dumps(payload, ensure_ascii=False)))
    fg.add_to(m)

    # postMessage to parent AND top (Folium may nest srcdoc iframes).
    binds_js = "\n".join(
        (
            f"try{{{mname}.on('click',function(){{"
            f"var __p={pj};"
            f"try{{window.parent&&window.parent.postMessage(__p,'*');}}catch(eP){{}}"
            f"try{{window.top&&window.top!==window&&window.top.postMessage(__p,'*');}}catch(eT){{}}"
            f"}});}}catch(eZ){{}}"
        )
        for mname, pj in click_payloads
    )

    class _Bind(folium.MacroElement):
        _template = Template(
            """
            {% macro script(this, kwargs) %}
            window.__efcLayerGroups = window.__efcLayerGroups || {};
            window.__efcLayerGroups.zentra = {{this.fg}};
            {{this.binds}}
            {% endmacro %}
            """
        )

        def __init__(self, fg_obj, binds: str):
            super().__init__()
            self.fg = fg_obj.get_name()
            self.binds = binds

    _Bind(fg, binds_js).add_to(m)
    return fg


def bind_stations_feature_group(m, fg) -> None:
    """Expose Estações FeatureGroup to the parent layer strip."""
    if m is None or fg is None:
        return
    import folium
    from jinja2 import Template

    class _Bind(folium.MacroElement):
        _template = Template(
            """
            {% macro script(this, kwargs) %}
            window.__efcLayerGroups = window.__efcLayerGroups || {};
            window.__efcLayerGroups.stations = {{this.fg}};
            {% endmacro %}
            """
        )

        def __init__(self, fg_obj):
            super().__init__()
            self.fg = fg_obj.get_name()

    _Bind(fg).add_to(m)
