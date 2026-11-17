"""Zentra (TEROS/ATMOS) catalog + last readings for map / Sensores aba."""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

# Sentinel / bad water-potential readings from ZENTRA when sensor dry/offline.
_WP_SENTINEL = -90000.0

# Series shown in map popup (compact) and sensors table.
POPUP_VARS: tuple[str, ...] = (
    "soil_vwc",
    "water_potential_kpa",
    "precip_mm",
    "battery_pct",
    "signal_pct",
)

VAR_LABELS_PT: dict[str, str] = {
    "soil_vwc": "VWC solo",
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


def _bq_dataset(dataset: str | None = None) -> str:
    return (dataset or os.environ.get("BQ_DATASET") or "efc_clima").strip() or "efc_clima"


def _client():
    try:
        from gcp.bq_load_clima import _bq_client

        return _bq_client()
    except Exception:
        return None


def _pfx(dataset: str) -> str:
    from gcp.bq_config import get_bq_fully_qualified_prefix

    return get_bq_fully_qualified_prefix(dataset)


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
    if ts is None:
        return "—" if lang_pt else "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    if age_h < 0:
        age_h = 0.0
    if age_h < 6:
        return "ok"
    if age_h < 24:
        return f"{age_h:.0f} h"
    return f"{age_h / 24.0:.0f} d"


def load_zentra_devices_df(*, bq_dataset: str | None = None):
    """Active Zentra loggers with valid coords (DataFrame for Folium / 3D)."""
    import pandas as pd

    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is None:
        return None
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
        if df is None or df.empty:
            return None
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        df = df.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
        return df if not df.empty else None
    except Exception:
        return None


def load_zentra_latest_by_location(
    *,
    bq_dataset: str | None = None,
    variables: tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    """location_id → {variable → {value, date}} for latest clima_series rows."""
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is None:
        return {}
    vars_ = variables or POPUP_VARS
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
        return out
    except Exception:
        return {}


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
        last_ts = None
        if last_dates:
            try:
                last_ts = max(last_dates)
            except Exception:
                last_ts = last_dates[0]
        if isinstance(last_ts, datetime):
            pass
        elif last_ts is not None:
            try:
                last_ts = datetime.fromisoformat(str(last_ts))
            except Exception:
                last_ts = None
        lm = r.get("last_measurement")
        try:
            lm_i = int(lm) if lm is not None and str(lm).strip() != "" else None
        except (TypeError, ValueError):
            lm_i = None
        if last_ts is None and lm_i:
            last_ts = datetime.fromtimestamp(lm_i, tz=timezone.utc)
        km = _nearest_km(lat, lon, main_line, cum_km)
        status = str(r.get("status") or "").strip() or "—"
        asset = str(r.get("asset_class") or "").strip() or "—"
        # Health summary from battery/signal when present
        bat = next((s for s in series if s["variable"] == "battery_pct"), None)
        sig = next((s for s in series if s["variable"] == "signal_pct"), None)
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
                "asset_class": asset,
                "sensors": sensors,
                "series": series,
                "health": " · ".join(health_bits) if health_bits else "—",
                "last_ts": last_ts.isoformat() if isinstance(last_ts, datetime) else None,
                "freshness": _freshness_label(last_ts, lang_pt=lang_pt),
                "map_url_qs": f"focus=zentra&lat={lat:.5f}&lon={lon:.5f}&zid={loc}",
            }
        )
    rows.sort(key=lambda x: (x.get("km") is None, x.get("km") or 0, x["name"]))
    return rows


def zentra_popup_html(row: dict[str, Any], *, lang_pt: bool = True) -> str:
    """Compact Folium popup: series + last value + freshness."""
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
    lines.append(f"<span style='color:#94a3b8'>atualizado {fresh}</span>")
    sensors = row.get("sensors") or []
    if sensors:
        lines.append(
            "<span style='color:#64748b'>"
            + " · ".join(sensors[:4])
            + "</span>"
        )
    return "<br>".join(lines)


def add_zentra_markers_to_map(m, rows: list[dict[str, Any]], *, lang_pt: bool = True):
    """Distinct coral circles on a dedicated FeatureGroup (toggleable)."""
    import folium
    from jinja2 import Template

    if not m or not rows:
        return None
    fg = folium.FeatureGroup(name="Zentra", show=True, control=False)
    color = "#f97316"  # coral/orange — distinct from BNDMET green / ANA blue / EWS amber fill
    for row in rows:
        lat, lon = row.get("lat"), row.get("lon")
        if lat is None or lon is None:
            continue
        body = zentra_popup_html(row, lang_pt=lang_pt)
        tip = f"Zentra — {row.get('name') or row.get('location_id')}"
        folium.CircleMarker(
            location=[float(lat), float(lon)],
            radius=7,
            color="#9a3412",
            fill=True,
            fillColor=color,
            fillOpacity=0.92,
            weight=2.0,
            popup=folium.Popup(body, max_width=240),
            tooltip=tip,
        ).add_to(fg)
    fg.add_to(m)

    class _Bind(folium.MacroElement):
        _template = Template(
            """
            {% macro script(this, kwargs) %}
            window.__efcLayerGroups = window.__efcLayerGroups || {};
            window.__efcLayerGroups.zentra = {{this.fg}};
            {% endmacro %}
            """
        )

        def __init__(self, fg_obj):
            super().__init__()
            self.fg = fg_obj.get_name()

    _Bind(fg).add_to(m)
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
