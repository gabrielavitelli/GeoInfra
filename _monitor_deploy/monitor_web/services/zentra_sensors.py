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

# Series shown in map popup (compact) and sensors table.
POPUP_VARS: tuple[str, ...] = (
    "soil_vwc",
    "water_potential_kpa",
    "precip_mm",
    "battery_pct",
    "signal_pct",
)

VAR_LABELS_PT: dict[str, str] = {
    "soil_vwc": "VWC: solo",
    "water_potential_kpa": "Pot. água",
    "precip_mm": "Precip",
    "battery_pct": "Bateria",
    "signal_pct": "Sinal",
    "soil_temp_c": "Temp solo",
}

VAR_UNITS: dict[str, str] = {
    "soil_vwc": "m³/m³",
    "water_potential_kpa": "kPa",
    "precip_mm": "mm",
    "battery_pct": "%",
    "signal_pct": "%",
    "soil_temp_c": "°C",
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
            }
        )
    rows.sort(key=lambda x: (x.get("km") is None, x.get("km") or 0, x["name"]))
    return rows


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
