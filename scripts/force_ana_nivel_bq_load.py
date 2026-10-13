#!/usr/bin/env python3
"""Force-refresh ANA nivel_m / vazao_m3s into climaticsystem.efc_clima.clima_series.

Uses the public ANA telemétrica SOAP/GET API (fresh through today), then upserts
rows into BigQuery. Prefer Cloud Run `efc-ingestion-stations` when available;
this script is the direct fallback once gcloud ADC is authenticated.

Usage:
  python scripts/force_ana_nivel_bq_load.py --days 35
  python scripts/force_ana_nivel_bq_load.py --days 35 --codes 29040000,29050000
"""
from __future__ import annotations

import argparse
import csv
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from google.cloud import bigquery

PROJECT = "climaticsystem"
DATASET = "efc_clima"
TABLE = "clima_series"
ANA_GET = "http://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"

# Default: EFC-corridor ANA codes previously seen in BQ + nearest to Km 661.
DEFAULT_CODES = [
    "23800495",
    "29040000",
    "28850000",
    "29050000",
    "28844995",
    "29130000",
    "29100000",
    "29110000",
    "23310000",
    "32540000",
    "32550000",
    "34070000",
    "31700000",
    "29680090",
    "23466000",
]


def fetch_ana(code: str, start: date, end: date) -> list[dict]:
    qs = urllib.parse.urlencode(
        {
            "codEstacao": code,
            "dataInicio": start.strftime("%d/%m/%Y"),
            "dataFim": end.strftime("%d/%m/%Y"),
        }
    )
    url = f"{ANA_GET}?{qs}"
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    root = ET.fromstring(data)
    rows: list[dict] = []
    for el in root.iter():
        if el.tag.endswith("DadosHidrometereologicos"):
            d = {c.tag.split("}")[-1]: (c.text or "").strip() for c in el}
            rows.append(d)
    return rows


def to_bq_rows(code: str, raw: list[dict], ingested_at: str) -> list[dict]:
    loc = f"estacao_{code}"
    # Aggregate to daily mean (matches clima_series date grain used elsewhere).
    nivel_by_day: dict[date, list[float]] = {}
    vazao_by_day: dict[date, list[float]] = {}
    for x in raw:
        try:
            ts = datetime.strptime(x["DataHora"][:19].strip(), "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        d = ts.date()
        if x.get("Nivel"):
            try:
                nivel_by_day.setdefault(d, []).append(float(x["Nivel"]) / 100.0)
            except ValueError:
                pass
        if x.get("Vazao"):
            try:
                vazao_by_day.setdefault(d, []).append(float(x["Vazao"]))
            except ValueError:
                pass

    out: list[dict] = []
    for d, vals in sorted(nivel_by_day.items()):
        out.append(
            {
                "date": d.isoformat(),
                "location_id": loc,
                "source": "ana",
                "ingested_at": ingested_at,
                "variable": "nivel_m",
                "value": round(sum(vals) / len(vals), 4),
            }
        )
    for d, vals in sorted(vazao_by_day.items()):
        out.append(
            {
                "date": d.isoformat(),
                "location_id": loc,
                "source": "ana",
                "ingested_at": ingested_at,
                "variable": "vazao_m3s",
                "value": round(sum(vals) / len(vals), 4),
            }
        )
    return out


def delete_window(client: bigquery.Client, codes: list[str], start: date, end: date) -> None:
    locs = ", ".join(f"'estacao_{c}'" for c in codes)
    sql = f"""
    DELETE FROM `{PROJECT}.{DATASET}.{TABLE}`
    WHERE source = 'ana'
      AND variable IN ('nivel_m', 'vazao_m3s')
      AND location_id IN ({locs})
      AND date BETWEEN @start AND @end
    """
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATE", start.isoformat()),
                bigquery.ScalarQueryParameter("end", "DATE", end.isoformat()),
            ]
        ),
    )
    job.result()
    print(f"deleted window {start}..{end} for {len(codes)} stations (job {job.job_id})")


def load_rows(client: bigquery.Client, rows: list[dict]) -> None:
    if not rows:
        print("no rows to load")
        return
    table = f"{PROJECT}.{DATASET}.{TABLE}"
    job = client.load_table_from_json(
        rows,
        table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        ),
    )
    job.result()
    print(f"loaded {len(rows)} rows → {table}")


def verify(client: bigquery.Client) -> None:
    sql = f"""
    SELECT variable, MAX(date) AS max_date, COUNT(*) AS n
    FROM `{PROJECT}.{DATASET}.{TABLE}`
    WHERE source = 'ana' AND variable IN ('nivel_m', 'vazao_m3s')
    GROUP BY variable
    ORDER BY variable
    """
    for row in client.query(sql).result():
        print(f"verify: {row.variable} max_date={row.max_date} n={row.n}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--codes", type=str, default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--csv-out", type=str, default="/tmp/ana_nivel_bq_rows.csv")
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or DEFAULT_CODES
    end = date.today()
    start = end - timedelta(days=args.days)
    ingested_at = datetime.now(timezone.utc).isoformat()

    all_rows: list[dict] = []
    for code in codes:
        try:
            raw = fetch_ana(code, start, end)
        except Exception as e:
            print(f"{code}: fetch error {e}")
            continue
        rows = to_bq_rows(code, raw, ingested_at)
        print(f"{code}: raw={len(raw)} bq_rows={len(rows)}")
        all_rows.extend(rows)

    Path(args.csv_out).parent.mkdir(parents=True, exist_ok=True)
    if all_rows:
        with open(args.csv_out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)
        print(f"wrote {args.csv_out}")

    if args.dry_run:
        print("dry-run: skip BQ")
        return

    client = bigquery.Client(project=PROJECT)
    delete_window(client, codes, start, end)
    load_rows(client, all_rows)
    verify(client)


if __name__ == "__main__":
    main()
