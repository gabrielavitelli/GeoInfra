#!/usr/bin/env python3
"""Checklist diário Raijin Alerta — fontes BQ + bell API.

Saída: JSON em stdout e opcionalmente GCS.
Exit 0 = core OK (EWS + GFS + MERGE frescos; ML opcional).
Exit 2 = core degradado.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

PROJECT = os.environ.get("GCP_PROJECT", "climaticsystem")
DATASET = os.environ.get("BQ_DATASET", "efc_clima")
BELL_URL = os.environ.get(
    "RAIJIN_BELL_URL",
    "https://monitor-efc-prod-zws527es5a-uc.a.run.app/api/home/raijin-alerts/?days=30",
)

# Limiares de lag (dias) para core health
CORE = {
    "gfs": 1,
    "orion": 1,
    "merge": 2,
}
OPTIONAL = {
    "ecmwf": 2,
    "icon": 2,
    "zentra": 2,
    "ana": 3,
    "estacoes": 3,
}


def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=PROJECT)


def source_meta() -> list[dict[str, Any]]:
    sql = f"""
    SELECT source,
           CAST(last_observation AS STRING) AS last_observation,
           CAST(last_ingested_at AS STRING) AS last_ingested_at,
           CAST(refreshed_at AS STRING) AS refreshed_at
    FROM `{PROJECT}.{DATASET}.clima_series_source_meta`
    ORDER BY source
    """
    rows = list(_bq().query(sql).result())
    out = []
    today = date.today()
    for r in rows:
        last = r["last_observation"]
        lag = None
        if last:
            try:
                lag = (today - date.fromisoformat(str(last)[:10])).days
            except ValueError:
                lag = None
        out.append(
            {
                "source": r["source"],
                "last_observation": last,
                "last_ingested_at": r["last_ingested_at"],
                "refreshed_at": r["refreshed_at"],
                "lag_days": lag,
            }
        )
    return out


def raijin_ml_freshness() -> dict[str, Any]:
    sql = f"""
    SELECT CAST(MAX(day) AS STRING) AS max_day, COUNT(*) AS n
    FROM `{PROJECT}.{DATASET}.raijin_alerts`
    WHERE day >= DATE_SUB(CURRENT_DATE(), INTERVAL 120 DAY)
    """
    row = list(_bq().query(sql).result())[0]
    max_day = row["max_day"]
    lag = None
    if max_day:
        lag = (date.today() - date.fromisoformat(str(max_day)[:10])).days
    return {"max_day": max_day, "n_120d": int(row["n"] or 0), "lag_days": lag}


def exceed_freshness() -> list[dict[str, Any]]:
    sql = f"""
    SELECT source, CAST(MAX(day) AS STRING) AS max_day, COUNT(*) AS n
    FROM `{PROJECT}.{DATASET}.monitor_daily_exceed`
    WHERE day >= DATE_SUB(CURRENT_DATE(), INTERVAL 40 DAY)
    GROUP BY source ORDER BY source
    """
    return [
        {"source": r["source"], "max_day": r["max_day"], "n": int(r["n"])}
        for r in _bq().query(sql).result()
    ]


def bell_snapshot() -> dict[str, Any]:
    """Bell API exige sessão; 401 sem cookie é esperado fora do portal."""
    try:
        req = urllib.request.Request(BELL_URL, headers={"User-Agent": "raijin-health/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return {
            "http": resp.status,
            "available": data.get("available"),
            "latest_day": data.get("latest_day"),
            "count": data.get("count"),
            "n_threshold": data.get("n_threshold"),
            "ews_ok": (data.get("ews_status") or {}).get("ok"),
            "auth_required": False,
        }
    except urllib.error.HTTPError as exc:
        return {
            "http": exc.code,
            "auth_required": exc.code in (401, 403),
            "error": str(exc),
            "note": "Use cookie de sessão do portal para validar o JSON do sino.",
        }
    except Exception as exc:
        return {"http": None, "error": str(exc)}


def evaluate(meta: list[dict[str, Any]], ml: dict[str, Any]) -> dict[str, Any]:
    by = {m["source"]: m for m in meta}
    core_status = {}
    core_ok = True
    for src, max_lag in CORE.items():
        m = by.get(src) or {}
        lag = m.get("lag_days")
        ok = lag is not None and lag <= max_lag
        core_status[src] = {"ok": ok, "lag_days": lag, "max_lag": max_lag}
        if not ok:
            core_ok = False
    ml_ok = ml.get("lag_days") is not None and ml["lag_days"] <= 2
    return {
        "core_ok": core_ok,
        "ml_ok": ml_ok,
        "core": core_status,
        "ml": ml,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gcs-out", default="", help="gs://…/raijin_health.json")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    meta = source_meta()
    ml = raijin_ml_freshness()
    exceed = exceed_freshness()
    bell = bell_snapshot()
    verdict = evaluate(meta, ml)
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "project": PROJECT,
        "dataset": DATASET,
        "verdict": verdict,
        "sources": meta,
        "exceed": exceed,
        "bell": bell,
    }
    raw = json.dumps(report, indent=2 if args.pretty else None, default=str)
    print(raw)
    if args.gcs_out:
        from google.cloud import storage

        _, _, rest = args.gcs_out.partition("gs://")
        bucket_name, _, blob_name = rest.partition("/")
        storage.Client().bucket(bucket_name).blob(blob_name).upload_from_string(
            raw, content_type="application/json"
        )
        print(f"wrote {args.gcs_out}", file=sys.stderr)
    return 0 if verdict["core_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
