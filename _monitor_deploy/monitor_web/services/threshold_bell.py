"""Excedências de limiar (EWS/MERGE) para o painel de alertas da visão geral."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_LIMIAR_ORDER = {"24h": 0, "5d": 1, "7d": 2, "30d": 3}


def _from_monitor_daily_exceed(
    *,
    dataset: str,
    days: int,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        from google.cloud import bigquery

        from gcp.bq_config import bq_to_dataframe
        from gcp.bq_monitor_daily import (
            MONITOR_DAILY_EXCEED_TABLE,
            _limiar_labels,
            monitor_daily_exceed_enabled,
            monitor_daily_exceed_table_exists,
        )
    except Exception:
        return []

    if not monitor_daily_exceed_enabled() or not monitor_daily_exceed_table_exists(dataset):
        return []

    client = bigquery.Client()
    proj = client.project
    fq = f"`{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}`"
    lookback = max(1, min(int(days), 120))
    lim = max(1, min(int(limit), 80))
    sql = f"""
    SELECT CAST(day AS STRING) AS day,
           CAST(km_efc AS INT64) AS km_efc,
           limiar,
           value_mm,
           source
    FROM {fq}
    WHERE day >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
      AND source IN ('ews', 'merge')
    ORDER BY day DESC, value_mm DESC
    LIMIT @lim
    """
    try:
        job = client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("days", "INT64", lookback),
                    bigquery.ScalarQueryParameter("lim", "INT64", lim),
                ]
            ),
        )
        df = bq_to_dataframe(job)
    except Exception as exc:
        logger.warning("threshold_bell monitor_daily_exceed: %s", exc)
        return []
    if df is None or df.empty:
        return []
    labels = _limiar_labels()
    out: list[dict[str, Any]] = []
    for r in df.itertuples(index=False):
        limiar = str(getattr(r, "limiar", "") or "")
        src = str(getattr(r, "source", "") or "ews").lower()
        out.append(
            {
                "day": str(r.day)[:10],
                "km": int(r.km_efc),
                "outcome": "ews" if src == "ews" else "limiar",
                "mm": round(float(r.value_mm), 1),
                "score": None,
                "limiar": labels.get(limiar, limiar),
                "source": src,
                "kind": "threshold",
            }
        )
    return out


def _from_live_orion(
    *,
    dataset: str,
    days: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Fallback: varre orion precip_mm recente e associa ao km se ultrapassar limiar 24h."""
    try:
        from google.cloud import bigquery

        from gcp.bq_config import bq_to_dataframe
        from gcp.bq_monitor_daily import THRESHOLD_24H_MM, THRESHOLD_7D_MM, THRESHOLD_30D_MM, THRESHOLD_120H_MM
        from lib_efc import get_railway_data, position_at_km
    except Exception:
        return []

    client = bigquery.Client()
    lookback = max(1, min(int(days), 45))
    sql = f"""
    WITH daily AS (
      SELECT
        location_id,
        DATE(date) AS d,
        SUM(slot_mm) AS daily_mm
      FROM (
        SELECT
          location_id,
          date,
          COALESCE(valid_time, TIMESTAMP(DATE(date))) AS slot_ts,
          MAX(value) AS slot_mm
        FROM `{client.project}.{dataset}.clima_series`
        WHERE source = 'orion'
          AND variable = 'precip_mm'
          AND DATE(date) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY) AND CURRENT_DATE()
          AND value <= 120
          AND NOT STARTS_WITH(location_id, 'ews_IMEI_')
        GROUP BY location_id, date, slot_ts
      )
      GROUP BY 1, 2
    ),
    win AS (
      SELECT
        location_id,
        d AS day,
        daily_mm AS mm_24,
        SUM(daily_mm) OVER (
          PARTITION BY location_id
          ORDER BY UNIX_DATE(d)
          RANGE BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS mm_5d,
        SUM(daily_mm) OVER (
          PARTITION BY location_id
          ORDER BY UNIX_DATE(d)
          RANGE BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS mm_7d,
        SUM(daily_mm) OVER (
          PARTITION BY location_id
          ORDER BY UNIX_DATE(d)
          RANGE BETWEEN 29 PRECEDING AND CURRENT ROW
        ) AS mm_30d
      FROM daily
    ),
    geo AS (
      SELECT w.*, l.lat, l.lon
      FROM win w
      INNER JOIN `{client.project}.{dataset}.locations` l USING (location_id)
      WHERE l.lat IS NOT NULL AND l.lon IS NOT NULL
    )
    SELECT location_id, CAST(day AS STRING) AS day, mm_24, mm_5d, mm_7d, mm_30d, lat, lon
    FROM geo
    WHERE mm_24 > @t24 OR mm_5d > @t5 OR mm_7d > @t7 OR mm_30d > @t30
    ORDER BY day DESC, mm_24 DESC
    LIMIT @lim
    """
    try:
        job = client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("days", "INT64", lookback),
                    bigquery.ScalarQueryParameter("t24", "FLOAT64", float(THRESHOLD_24H_MM)),
                    bigquery.ScalarQueryParameter("t5", "FLOAT64", float(THRESHOLD_120H_MM)),
                    bigquery.ScalarQueryParameter("t7", "FLOAT64", float(THRESHOLD_7D_MM)),
                    bigquery.ScalarQueryParameter("t30", "FLOAT64", float(THRESHOLD_30D_MM)),
                    bigquery.ScalarQueryParameter("lim", "INT64", max(1, min(int(limit), 80))),
                ]
            ),
        )
        df = bq_to_dataframe(job)
    except Exception as exc:
        logger.warning("threshold_bell live orion: %s", exc)
        return []
    if df is None or df.empty:
        return []

    main_line, cum_km, _mk, _lines = get_railway_data()
    out: list[dict[str, Any]] = []
    for r in df.itertuples(index=False):
        lat = float(getattr(r, "lat", 0) or 0)
        lon = float(getattr(r, "lon", 0) or 0)
        km = None
        if main_line is not None and cum_km is not None:
            # nearest sampled km every 5 km
            best_d = 1e18
            km_max = int(float(cum_km[-1]))
            for k in range(0, km_max + 1, 5):
                pos = position_at_km(float(k), main_line, cum_km)
                if pos is None:
                    continue
                d = (pos[0] - lat) ** 2 + (pos[1] - lon) ** 2
                if d < best_d:
                    best_d = d
                    km = k
        mm_24 = float(getattr(r, "mm_24", 0) or 0)
        mm_5d = float(getattr(r, "mm_5d", 0) or 0)
        mm_7d = float(getattr(r, "mm_7d", 0) or 0)
        mm_30d = float(getattr(r, "mm_30d", 0) or 0)
        if mm_24 > float(THRESHOLD_24H_MM):
            limiar, mm = "24h > 49 mm", mm_24
        elif mm_5d > float(THRESHOLD_120H_MM):
            limiar, mm = "5d", mm_5d
        elif mm_7d > float(THRESHOLD_7D_MM):
            limiar, mm = "7d", mm_7d
        else:
            limiar, mm = "30d", mm_30d
        out.append(
            {
                "day": str(r.day)[:10],
                "km": int(km) if km is not None else 0,
                "outcome": "ews",
                "mm": round(mm, 1),
                "score": None,
                "limiar": limiar,
                "source": "ews",
                "kind": "threshold",
                "station_id": str(getattr(r, "location_id", "") or ""),
            }
        )
    return out


def get_recent_threshold_alerts(
    *,
    dataset: str = "efc_clima",
    days: int = 30,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Últimas ultrapassagens de limiar (EWS preferencialmente) para o sino."""
    rows = _from_monitor_daily_exceed(dataset=dataset, days=days, limit=limit)
    if not rows:
        rows = _from_live_orion(dataset=dataset, days=min(days, 35), limit=limit)
    return rows


def threshold_status_snapshot(*, dataset: str = "efc_clima") -> dict[str, Any]:
    """Números atuais EWS vs limiares (para diagnóstico / painel)."""
    try:
        from google.cloud import bigquery

        from gcp.bq_monitor_daily import (
            THRESHOLD_24H_MM,
            THRESHOLD_7D_MM,
            THRESHOLD_30D_MM,
            THRESHOLD_120H_MM,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    client = bigquery.Client()
    sql = f"""
    WITH daily AS (
      SELECT location_id, DATE(date) AS d, SUM(slot_mm) AS daily_mm
      FROM (
        SELECT location_id, date, COALESCE(valid_time, TIMESTAMP(DATE(date))) AS slot_ts, MAX(value) AS slot_mm
        FROM `{client.project}.{dataset}.clima_series`
        WHERE source='orion' AND variable='precip_mm'
          AND DATE(date) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 29 DAY) AND CURRENT_DATE()
          AND value <= 120 AND NOT STARTS_WITH(location_id, 'ews_IMEI_')
        GROUP BY 1,2,3
      ) GROUP BY 1,2
    ),
    win AS (
      SELECT location_id,
        SUM(IF(d = CURRENT_DATE() OR d = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY), daily_mm, 0)) AS mm_24ish,
        SUM(IF(d BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 4 DAY) AND CURRENT_DATE(), daily_mm, 0)) AS mm_5d,
        SUM(IF(d BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY) AND CURRENT_DATE(), daily_mm, 0)) AS mm_7d,
        SUM(daily_mm) AS mm_30d,
        MAX(daily_mm) AS max_day
      FROM daily GROUP BY 1
    )
    SELECT
      COUNT(*) AS n_stations,
      COUNTIF(mm_24ish > @t24) AS n_above_24h,
      COUNTIF(mm_5d > @t5) AS n_above_5d,
      COUNTIF(mm_7d > @t7) AS n_above_7d,
      COUNTIF(mm_30d > @t30) AS n_above_30d,
      ROUND(MAX(mm_24ish),1) AS max_24ish,
      ROUND(MAX(mm_5d),1) AS max_5d,
      ROUND(MAX(mm_7d),1) AS max_7d,
      ROUND(MAX(mm_30d),1) AS max_30d,
      ROUND(MAX(max_day),1) AS max_day
    FROM win
    """
    try:
        row = list(
            client.query(
                sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("t24", "FLOAT64", float(THRESHOLD_24H_MM)),
                        bigquery.ScalarQueryParameter("t5", "FLOAT64", float(THRESHOLD_120H_MM)),
                        bigquery.ScalarQueryParameter("t7", "FLOAT64", float(THRESHOLD_7D_MM)),
                        bigquery.ScalarQueryParameter("t30", "FLOAT64", float(THRESHOLD_30D_MM)),
                    ]
                ),
            ).result()
        )[0]
        return {
            "ok": True,
            "thresholds": {
                "24h": float(THRESHOLD_24H_MM),
                "5d": float(THRESHOLD_120H_MM),
                "7d": float(THRESHOLD_7D_MM),
                "30d": float(THRESHOLD_30D_MM),
            },
            "n_stations": int(row.n_stations or 0),
            "n_above": {
                "24h": int(row.n_above_24h or 0),
                "5d": int(row.n_above_5d or 0),
                "7d": int(row.n_above_7d or 0),
                "30d": int(row.n_above_30d or 0),
            },
            "max_mm": {
                "24h": float(row.max_24ish or 0),
                "5d": float(row.max_5d or 0),
                "7d": float(row.max_7d or 0),
                "30d": float(row.max_30d or 0),
                "day": float(row.max_day or 0),
            },
            "as_of": date.today().isoformat(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
