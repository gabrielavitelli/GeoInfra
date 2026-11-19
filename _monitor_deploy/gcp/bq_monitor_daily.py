"""
Tabela monitor_daily_exceed — excedências pré-calculadas para leitura rápida do portal.

Refresh (batch/cron): refresh_monitor_daily_exceed_range()
Leitura (home): load_monitor_daily_exceed_df()
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import pandas as pd

from gcp.bq_config import EFC_RAILWAY_LOCATIONS_WHERE, bq_to_dataframe

logger = logging.getLogger(__name__)

MONITOR_DAILY_EXCEED_TABLE = "monitor_daily_exceed"
THRESHOLD_24H_MM = 49.0
THRESHOLD_120H_MM = 250.0
THRESHOLD_7D_MM = 150.0
THRESHOLD_30D_MM = 465.0

_LIMIAR_TO_LABEL = {
    "24h": None,  # resolved via lib_efc at runtime
    "5d": None,
    "7d": None,
    "30d": None,
}


def monitor_daily_exceed_enabled() -> bool:
    return os.environ.get("MONITOR_USE_DAILY_EXCEED_TABLE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _bq_client(project: str | None = None):
    from google.cloud import bigquery

    return bigquery.Client(project=project)


def _fq_table(dataset: str, project: str | None = None) -> str:
    client = _bq_client(project)
    proj = project or client.project
    return f"`{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}`"


def monitor_daily_exceed_table_exists(dataset: str = "efc_clima", project: str | None = None) -> bool:
    try:
        client = _bq_client(project)
        proj = project or client.project
        client.get_table(f"{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}")
        return True
    except Exception:
        return False


def _limiar_labels() -> dict[str, str]:
    from lib_efc import limiar_label_24h, limiar_label_5d, limiar_label_7d, limiar_label_30d

    return {
        "24h": limiar_label_24h(),
        "5d": limiar_label_5d(),
        "7d": limiar_label_7d(),
        "30d": limiar_label_30d(),
    }


def monitor_daily_exceed_to_dataframe(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Converte linhas BQ → formato table_exceedance (Limiar, Quilometragem, Valor, Dia)."""
    cols = ["Limiar", "Quilometragem (km)", "Valor (mm)", "Dia"]
    if df_raw is None or df_raw.empty:
        return pd.DataFrame(columns=cols)
    labels = _limiar_labels()
    rows = []
    for _, r in df_raw.iterrows():
        lim = str(r.get("limiar") or "")
        rows.append(
            {
                "Limiar": labels.get(lim, lim),
                "Quilometragem (km)": float(r.get("km_efc", 0)),
                "Valor (mm)": round(float(r.get("value_mm", 0)), 1),
                "Dia": str(r.get("day", ""))[:10],
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=cols)
    return out.sort_values(["Limiar", "Quilometragem (km)"]).reset_index(drop=True)


def load_monitor_daily_exceed_df(
    ref_date: str,
    *,
    dataset: str = "efc_clima",
    project: str | None = None,
    source: str = "merge",
    include_ews: bool = True,
) -> pd.DataFrame | None:
    """Lê excedências do dia — query leve (~KB). Retorna None se tabela ausente ou erro.

    Com ``include_ews=True`` e ``source='merge'``, une ultrapassagens Orion/EWS
    (source=ews) no mesmo painel — max(value) por (km, limiar).
    """
    if not monitor_daily_exceed_enabled():
        return None
    if not monitor_daily_exceed_table_exists(dataset, project):
        return None
    ref = str(ref_date)[:10]
    fq = _fq_table(dataset, project)
    client = _bq_client(project)
    from google.cloud import bigquery

    sources = [source]
    if include_ews and str(source).lower() == "merge":
        sources.append("ews")
    sql = f"""
    SELECT day, km_efc, limiar, MAX(value_mm) AS value_mm, ANY_VALUE(source) AS source
    FROM {fq}
    WHERE day = @ref_date AND source IN UNNEST(@sources)
    GROUP BY day, km_efc, limiar
    ORDER BY limiar, km_efc
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ref_date", "DATE", ref),
            bigquery.ArrayQueryParameter("sources", "STRING", sources),
        ]
    )
    try:
        df = bq_to_dataframe(client.query(sql, job_config=job_config))
        return monitor_daily_exceed_to_dataframe(df)
    except Exception as exc:
        logger.warning("load_monitor_daily_exceed_df %s: %s", ref, exc)
        return None


def refresh_monitor_daily_exceed_for_day(
    ref_date: str,
    *,
    dataset: str = "efc_clima",
    project: str | None = None,
    source: str = "merge",
    include_rolling: bool = True,
) -> int:
    """
    Recalcula e substitui excedências do dia na tabela monitor_daily_exceed.
    Retorna número de linhas inseridas.
    """
    if source == "ensemble":
        return _refresh_ensemble_exceed_for_day(
            ref_date,
            dataset=dataset,
            project=project,
            include_rolling=include_rolling,
        )
    client = _bq_client(project)
    proj = project or client.project
    ref = str(ref_date)[:10]
    fq_exceed = f"`{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}`"
    full_series = f"`{proj}.{dataset}.clima_series`"
    full_locations = f"`{proj}.{dataset}.locations`"

    delete_sql = f"""
    DELETE FROM {fq_exceed}
    WHERE day = DATE(@ref_date) AND source = @src
    """
    from google.cloud import bigquery

    params = [
        bigquery.ScalarQueryParameter("ref_date", "STRING", ref),
        bigquery.ScalarQueryParameter("src", "STRING", source),
    ]
    client.query(delete_sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

    # MERGE operacional é precip_mm_6h_* (precip_24h_mm congelou em 2026-05).
    # Agrega mm/dia com a mesma regra de bq_reader_extended._merge_daily_mm_sql.
    if str(source).lower() == "merge":
        _refresh_merge_exceed_from_6h(
            client,
            proj,
            dataset,
            ref,
            include_rolling=include_rolling,
        )
    else:
        insert_instant = f"""
        INSERT INTO {fq_exceed} (day, km_efc, limiar, value_mm, source, ingested_at)
        SELECT
          s.date AS day,
          CAST(ROUND(l.km_efc) AS INT64) AS km_efc,
          CASE
            WHEN s.variable = 'precip_24h_mm' THEN '24h'
            WHEN s.variable IN ('precip_120h_mm', 'precip_72h_mm') THEN '5d'
          END AS limiar,
          s.value AS value_mm,
          s.source,
          CURRENT_TIMESTAMP()
        FROM {full_series} s
        INNER JOIN {full_locations} l ON l.location_id = s.location_id
        WHERE s.date = DATE(@ref_date)
          AND s.source = @src
          AND l.km_efc IS NOT NULL
          AND ({EFC_RAILWAY_LOCATIONS_WHERE.replace('source', 'l.source')})
          AND s.variable IN ('precip_24h_mm', 'precip_120h_mm', 'precip_72h_mm')
          AND (
            (s.variable = 'precip_24h_mm' AND s.value > {THRESHOLD_24H_MM})
            OR (s.variable IN ('precip_120h_mm', 'precip_72h_mm') AND s.value > {THRESHOLD_120H_MM})
          )
        """
        client.query(insert_instant, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

        if include_rolling:
            _refresh_rolling_exceed(client, proj, dataset, ref, source)

    # Também sincroniza ultrapassagens por pluviômetros Orion/EWS → km mais próximo.
    if str(source).lower() == "merge":
        try:
            refresh_ews_station_exceed_for_day(
                ref, dataset=dataset, project=project, include_rolling=include_rolling
            )
        except Exception as exc:
            logger.warning("refresh_ews_station_exceed_for_day %s: %s", ref, exc)

    count_sql = f"SELECT COUNT(*) AS n FROM {fq_exceed} WHERE day = DATE(@ref_date) AND source = @src"
    row = list(client.query(count_sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())[0]
    return int(row.n)


def refresh_ews_station_exceed_for_day(
    ref_date: str,
    *,
    dataset: str = "efc_clima",
    project: str | None = None,
    include_rolling: bool = True,
) -> int:
    """
    Excedências de limiar a partir de precipitação Orion/EWS, associadas ao km EFC
    mais próximo (mesma lógica estação→km de BNDMET/ANA).
    Grava ``source='ews'`` em ``monitor_daily_exceed``.
    """
    from google.cloud import bigquery

    client = _bq_client(project)
    proj = project or client.project
    ref = str(ref_date)[:10]
    fq_exceed = f"`{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}`"
    full_series = f"`{proj}.{dataset}.clima_series`"
    full_locations = f"`{proj}.{dataset}.locations`"
    src = "ews"
    params = [
        bigquery.ScalarQueryParameter("ref_date", "STRING", ref),
        bigquery.ScalarQueryParameter("src", "STRING", src),
    ]
    delete_sql = f"""
    DELETE FROM {fq_exceed}
    WHERE day = DATE(@ref_date) AND source = @src
    """
    client.query(delete_sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

    # Acumulados 24h / 5d / 7d / 30d por estação Orion no dia ref, depois join ao km
    # ferroviário mais próximo (haversine aproximado via lat/lon em locations).
    insert_sql = f"""
    INSERT INTO {fq_exceed} (day, km_efc, limiar, value_mm, source, ingested_at)
    WITH station_daily AS (
      SELECT
        REGEXP_REPLACE(s.location_id, r'^ews_', '') AS cod,
        s.location_id,
        DATE(s.date) AS d,
        SUM(slot_mm) AS daily_mm
      FROM (
        SELECT
          location_id,
          date,
          COALESCE(valid_time, TIMESTAMP(DATE(date))) AS slot_ts,
          MAX(value) AS slot_mm
        FROM {full_series}
        WHERE source = 'orion'
          AND variable = 'precip_mm'
          AND DATE(date) BETWEEN DATE_SUB(DATE(@ref_date), INTERVAL 29 DAY) AND DATE(@ref_date)
          AND value <= 120
          AND NOT STARTS_WITH(location_id, 'ews_IMEI_')
        GROUP BY location_id, date, slot_ts
      ) s
      GROUP BY 1, 2, 3
    ),
    station_win AS (
      SELECT
        location_id,
        SUM(IF(d = DATE(@ref_date), daily_mm, 0)) AS mm_24,
        SUM(IF(d BETWEEN DATE_SUB(DATE(@ref_date), INTERVAL 4 DAY) AND DATE(@ref_date), daily_mm, 0)) AS mm_5d,
        SUM(IF(d BETWEEN DATE_SUB(DATE(@ref_date), INTERVAL 6 DAY) AND DATE(@ref_date), daily_mm, 0)) AS mm_7d,
        SUM(daily_mm) AS mm_30d
      FROM station_daily
      GROUP BY location_id
    ),
    station_geo AS (
      SELECT
        w.location_id,
        w.mm_24, w.mm_5d, w.mm_7d, w.mm_30d,
        l.lat, l.lon
      FROM station_win w
      INNER JOIN {full_locations} l ON l.location_id = w.location_id
      WHERE l.lat IS NOT NULL AND l.lon IS NOT NULL
    ),
    rail AS (
      SELECT
        CAST(ROUND(km_efc) AS INT64) AS km_efc,
        lat, lon
      FROM {full_locations}
      WHERE source = 'railway_km' AND lat IS NOT NULL AND lon IS NOT NULL AND km_efc IS NOT NULL
    ),
    nearest AS (
      SELECT
        g.location_id,
        g.mm_24, g.mm_5d, g.mm_7d, g.mm_30d,
        ARRAY_AGG(r.km_efc ORDER BY
          POW(g.lat - r.lat, 2) + POW(g.lon - r.lon, 2)
          LIMIT 1
        )[OFFSET(0)] AS km_efc
      FROM station_geo g
      CROSS JOIN rail r
      GROUP BY g.location_id, g.mm_24, g.mm_5d, g.mm_7d, g.mm_30d
    ),
    peaked AS (
      SELECT km_efc, '24h' AS limiar, MAX(mm_24) AS value_mm FROM nearest
      WHERE mm_24 > {THRESHOLD_24H_MM} GROUP BY km_efc
      UNION ALL
      SELECT km_efc, '5d', MAX(mm_5d) FROM nearest
      WHERE mm_5d > {THRESHOLD_120H_MM} GROUP BY km_efc
      UNION ALL
      SELECT km_efc, '7d', MAX(mm_7d) FROM nearest
      WHERE mm_7d > {THRESHOLD_7D_MM} GROUP BY km_efc
      UNION ALL
      SELECT km_efc, '30d', MAX(mm_30d) FROM nearest
      WHERE mm_30d > {THRESHOLD_30D_MM} GROUP BY km_efc
    )
    SELECT DATE(@ref_date), km_efc, limiar, value_mm, @src, CURRENT_TIMESTAMP()
    FROM peaked
    """
    _ = include_rolling  # janelas 5/7/30d já cobertas acima
    client.query(insert_sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    count_sql = f"SELECT COUNT(*) AS n FROM {fq_exceed} WHERE day = DATE(@ref_date) AND source = @src"
    row = list(client.query(count_sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())[0]
    return int(row.n)


def _refresh_ensemble_exceed_for_day(
    ref_date: str,
    *,
    dataset: str = "efc_clima",
    project: str | None = None,
    include_rolling: bool = True,
) -> int:
    """Excedências ensemble (mediana entre modelos) via bq_reader_clima."""
    from google.cloud import bigquery

    from gcp.bq_reader_clima import (
        load_efc_precip_from_bq_date_range,
        table_exceedance_operational_from_bq,
    )

    client = _bq_client(project)
    proj = project or client.project
    ref = str(ref_date)[:10]
    fq_exceed = f"`{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}`"
    source = "ensemble"
    lookback = (pd.Timestamp(ref) - pd.Timedelta(days=29)).strftime("%Y-%m-%d")

    params = [
        bigquery.ScalarQueryParameter("ref_date", "STRING", ref),
        bigquery.ScalarQueryParameter("src", "STRING", source),
    ]
    delete_sql = f"""
    DELETE FROM {fq_exceed}
    WHERE day = DATE(@ref_date) AND source = @src
    """
    client.query(delete_sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

    df_series, df_locs = load_efc_precip_from_bq_date_range(
        dataset=dataset,
        project=project,
        source="all",
        date_start=lookback if include_rolling else ref,
        date_end=ref,
        dedupe_models=False,
    )
    if df_locs is None:
        return 0
    if df_series is None:
        df_series = pd.DataFrame()
    df_day = table_exceedance_operational_from_bq(
        df_series,
        df_locs,
        ref,
        THRESHOLD_24H_MM,
        THRESHOLD_120H_MM,
        THRESHOLD_7D_MM,
        THRESHOLD_30D_MM,
    )
    if df_day.empty:
        return 0

    labels = _limiar_labels()
    inv_labels = {v: k for k, v in labels.items()}
    rows = []
    for _, r in df_day.iterrows():
        lim = inv_labels.get(str(r.get("Limiar", "")), str(r.get("Limiar", "")))
        rows.append(
            {
                "day": ref,
                "km_efc": int(round(float(r["Quilometragem (km)"]))),
                "limiar": lim,
                "value_mm": float(r["Valor (mm)"]),
                "source": source,
            }
        )
    if not rows:
        return 0
    df_ins = pd.DataFrame(rows)
    df_ins["ingested_at"] = pd.Timestamp.utcnow()
    table_id = f"{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}"
    job = client.load_table_from_dataframe(
        df_ins,
        table_id,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
    )
    job.result()
    return len(rows)


def _refresh_merge_exceed_from_6h(
    client,
    proj: str,
    dataset: str,
    ref: str,
    *,
    include_rolling: bool = True,
) -> int:
    """Excedências MERGE a partir de precip_mm_6h_* (+ precip_24h_mm legado)."""
    from google.cloud import bigquery

    from gcp.bq_reader_extended import _merge_6h_variable_predicate, _merge_daily_mm_sql

    fq_exceed = f"`{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}`"
    full_series = f"`{proj}.{dataset}.clima_series`"
    full_locations = f"`{proj}.{dataset}.locations`"
    merge_pred = _merge_6h_variable_predicate("s")
    merge_daily = _merge_daily_mm_sql("mm24", table_alias="s", value_col="value")
    params = [
        bigquery.ScalarQueryParameter("ref_date", "STRING", ref),
        bigquery.ScalarQueryParameter("src", "STRING", "merge"),
    ]
    lookback_clause = (
        "s.date BETWEEN DATE_SUB(DATE(@ref_date), INTERVAL 29 DAY) AND DATE(@ref_date)"
        if include_rolling
        else "s.date = DATE(@ref_date)"
    )
    # Lookback 29d para rolling; 5d = soma 5 dias civis quando não há precip_120h.
    sql = f"""
    INSERT INTO {fq_exceed} (day, km_efc, limiar, value_mm, source, ingested_at)
    WITH merge_src AS (
      SELECT
        CAST(ROUND(l.km_efc) AS INT64) AS km_efc,
        s.date,
        s.variable,
        s.value
      FROM {full_series} s
      INNER JOIN {full_locations} l ON l.location_id = s.location_id
      WHERE s.source = 'merge'
        AND l.km_efc IS NOT NULL
        AND ({EFC_RAILWAY_LOCATIONS_WHERE.replace('source', 'l.source')})
        AND {lookback_clause}
        AND (
          s.variable IN ('precip_24h_mm', 'precip_120h_mm', 'precip_72h_mm')
          OR {merge_pred}
        )
    ),
    per_var AS (
      SELECT km_efc, date, variable, MAX(value) AS value
      FROM merge_src
      GROUP BY km_efc, date, variable
    ),
    daily_24 AS (
      SELECT km_efc, date, {merge_daily}
      FROM per_var s
      GROUP BY km_efc, date
    ),
    roll AS (
      SELECT
        date,
        km_efc,
        mm24,
        SUM(mm24) OVER (
          PARTITION BY km_efc ORDER BY date
          ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS sum5,
        SUM(mm24) OVER (
          PARTITION BY km_efc ORDER BY date
          ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS sum7,
        SUM(mm24) OVER (
          PARTITION BY km_efc ORDER BY date
          ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) AS sum30
      FROM daily_24
    ),
    peaked AS (
      SELECT km_efc, '24h' AS limiar, mm24 AS value_mm
      FROM roll
      WHERE date = DATE(@ref_date) AND mm24 > {THRESHOLD_24H_MM}
      UNION ALL
      SELECT km_efc, '5d',
        COALESCE(
          (SELECT MAX(value) FROM per_var p
           WHERE p.km_efc = roll.km_efc AND p.date = DATE(@ref_date)
             AND p.variable IN ('precip_120h_mm', 'precip_72h_mm')),
          sum5
        )
      FROM roll
      WHERE date = DATE(@ref_date)
        AND COALESCE(
          (SELECT MAX(value) FROM per_var p
           WHERE p.km_efc = roll.km_efc AND p.date = DATE(@ref_date)
             AND p.variable IN ('precip_120h_mm', 'precip_72h_mm')),
          sum5
        ) > {THRESHOLD_120H_MM}
      UNION ALL
      SELECT km_efc, '7d', sum7
      FROM roll
      WHERE date = DATE(@ref_date) AND sum7 > {THRESHOLD_7D_MM}
      UNION ALL
      SELECT km_efc, '30d', sum30
      FROM roll
      WHERE date = DATE(@ref_date) AND sum30 > {THRESHOLD_30D_MM}
    )
    SELECT DATE(@ref_date), km_efc, limiar, value_mm, @src, CURRENT_TIMESTAMP()
    FROM peaked
    """
    client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    return 0


def _refresh_rolling_exceed(client, proj: str, dataset: str, ref: str, source: str) -> int:
    """7d e 30d acumulados no dia ref (modelos com precip_24h_mm por km)."""
    fq_exceed = f"`{proj}.{dataset}.{MONITOR_DAILY_EXCEED_TABLE}`"
    full_series = f"`{proj}.{dataset}.clima_series`"
    full_locations = f"`{proj}.{dataset}.locations`"
    from google.cloud import bigquery

    params = [
        bigquery.ScalarQueryParameter("ref_date", "STRING", ref),
        bigquery.ScalarQueryParameter("src", "STRING", source),
    ]
    rolling_sql = f"""
    INSERT INTO {fq_exceed} (day, km_efc, limiar, value_mm, source, ingested_at)
    WITH daily AS (
      SELECT
        s.date,
        CAST(ROUND(l.km_efc) AS INT64) AS km_efc,
        MAX(IF(s.variable = 'precip_24h_mm', s.value, NULL)) AS mm24
      FROM {full_series} s
      INNER JOIN {full_locations} l ON l.location_id = s.location_id
      WHERE s.source = @src
        AND s.variable = 'precip_24h_mm'
        AND l.km_efc IS NOT NULL
        AND ({EFC_RAILWAY_LOCATIONS_WHERE.replace('source', 'l.source')})
        AND s.date BETWEEN DATE_SUB(DATE(@ref_date), INTERVAL 29 DAY) AND DATE(@ref_date)
      GROUP BY s.date, km_efc
    ),
    roll AS (
      SELECT
        date,
        km_efc,
        SUM(mm24) OVER (
          PARTITION BY km_efc ORDER BY date
          ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS sum7,
        SUM(mm24) OVER (
          PARTITION BY km_efc ORDER BY date
          ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) AS sum30
      FROM daily
    )
    SELECT DATE(@ref_date), km_efc, '7d', sum7, @src, CURRENT_TIMESTAMP()
    FROM roll WHERE date = DATE(@ref_date) AND sum7 > {THRESHOLD_7D_MM}
    UNION ALL
    SELECT DATE(@ref_date), km_efc, '30d', sum30, @src, CURRENT_TIMESTAMP()
    FROM roll WHERE date = DATE(@ref_date) AND sum30 > {THRESHOLD_30D_MM}
    """
    job = client.query(rolling_sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
    job.result()
    return 0


def refresh_monitor_daily_exceed_range(
    *,
    dataset: str = "efc_clima",
    project: str | None = None,
    source: str = "merge",
    days_back: int = 7,
    end_date: str | None = None,
    include_rolling: bool = True,
) -> dict[str, int]:
    """Refresh dos últimos N dias (cron diário)."""
    end = date.fromisoformat(str(end_date)[:10]) if end_date else date.today()
    counts: dict[str, int] = {}
    for i in range(max(1, int(days_back))):
        d = (end - timedelta(days=i)).isoformat()
        try:
            counts[d] = refresh_monitor_daily_exceed_for_day(
                d,
                dataset=dataset,
                project=project,
                source=source,
                include_rolling=include_rolling,
            )
        except Exception as exc:
            logger.warning("refresh_monitor_daily_exceed %s: %s", d, exc)
            counts[d] = -1
    return counts
