#!/usr/bin/env python3
"""Force-refresh ANA nivel_m / vazao_m3s into climaticsystem.efc_clima.clima_series.

Primary: HidroInfo ANA (`www.ana.gov.br/hidrowebservice`, OAuth).
Fallback: public telemétrica GET (`telemetriaws1.../DadosHidrometeorologicos`).

HidroInfo has been returning intermittent 503s from Cloud Run
`efc-ingestion-stations`; this script is the direct catch-up path.

Usage:
  python scripts/force_ana_nivel_bq_load.py --days 30
  python scripts/force_ana_nivel_bq_load.py --days 30 --codes 29040000,28850000
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from google.cloud import bigquery

PROJECT = "climaticsystem"
DATASET = "efc_clima"
TABLE = "clima_series"
HIDROINFO_BASE = (
    os.environ.get("ANA_HIDROINFO_BASE_URL") or "https://www.ana.gov.br/hidrowebservice"
).rstrip("/")
ANA_GET = (
    os.environ.get("ANA_TELEMETRIA_GET_URL")
    or "http://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"
)

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


def _log(msg: str) -> None:
    print(msg, flush=True)


def _secret(name: str) -> str:
    env_map = {
        "ana-hidroinfo-identificador": "ANA_HIDROINFO_IDENTIFICADOR",
        "ana-hidroinfo-senha": "ANA_HIDROINFO_SENHA",
    }
    env_key = env_map.get(name, "")
    if env_key and os.environ.get(env_key):
        return os.environ[env_key].strip()
    try:
        return subprocess.check_output(
            [
                "gcloud",
                "secrets",
                "versions",
                "access",
                "latest",
                "--secret",
                name,
                f"--project={PROJECT}",
            ],
            text=True,
            timeout=60,
        ).strip()
    except Exception as exc:
        _log(f"secret {name} unavailable: {exc}")
        return ""


def hidroinfo_token() -> str:
    direct = (os.environ.get("ANA_HIDROINFO_TOKEN") or "").strip()
    if direct:
        return direct
    ident = _secret("ana-hidroinfo-identificador")
    senha = _secret("ana-hidroinfo-senha")
    if not ident or not senha:
        raise RuntimeError("ANA HidroInfo credentials missing")
    req = urllib.request.Request(
        f"{HIDROINFO_BASE}/EstacoesTelemetricas/OAUth/v1",
        headers={
            "Accept": "application/json",
            "Identificador": ident,
            "Senha": senha,
            "User-Agent": "efc-force-ana/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, dict):
        for key in ("tokenautenticacao", "token", "access_token"):
            tok = str(items.get(key) or "").strip()
            if tok:
                return tok
    raise RuntimeError(f"HidroInfo auth without token: {payload}")


def fetch_hidroinfo(code: str, end: date, days: int, token: str) -> list[dict]:
    """Return list of {DataHora, Nivel_cm?, Vazao?, Cota?} from HidroInfo adotada."""
    params = urllib.parse.urlencode(
        {
            "Código da Estação": str(code).strip(),
            "Tipo Filtro Data": "DATA_ULTIMA_ATUALIZACAO",
            "Data de Busca (yyyy-MM-dd)": end.strftime("%Y-%m-%dT00:00:00"),
            "Range Intervalo de busca": f"DIAS_{max(1, min(30, int(days)))}",
        }
    )
    url = f"{HIDROINFO_BASE}/EstacoesTelemetricas/HidroinfoanaSerieTelemetricaAdotada/v1?{params}"
    last_err: Exception | None = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "efc-force-ana/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.load(resp)
            items = payload.get("items") if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                return []
            rows: list[dict] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                dh = (
                    it.get("Data_Hora_Medicao")
                    or it.get("Data_Hora")
                    or it.get("DataHora")
                    or ""
                )
                cota = it.get("Cota_Adotada")
                if cota is None:
                    cota = it.get("Cota")
                vazao = it.get("Vazao_Adotada")
                if vazao is None:
                    vazao = it.get("Vazao")
                rows.append(
                    {
                        "DataHora": str(dh).replace(".0", "")[:19],
                        "Nivel": str(cota).strip() if cota not in (None, "") else "",
                        "Vazao": str(vazao).strip() if vazao not in (None, "") else "",
                    }
                )
            return rows
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < 5:
                wait = min(45.0, 1.8**attempt)
                _log(f"  hidroinfo {code}: HTTP {exc.code}, retry in {wait:.1f}s")
                time.sleep(wait)
                continue
            raise
        except Exception as exc:
            last_err = exc
            if attempt < 5:
                wait = min(45.0, 1.8**attempt)
                _log(f"  hidroinfo {code}: {exc}, retry in {wait:.1f}s")
                time.sleep(wait)
                continue
            raise
    assert last_err is not None
    raise last_err


def fetch_legacy(code: str, start: date, end: date) -> list[dict]:
    qs = urllib.parse.urlencode(
        {
            "codEstacao": code,
            "dataInicio": start.strftime("%d/%m/%Y"),
            "dataFim": end.strftime("%d/%m/%Y"),
        }
    )
    url = f"{ANA_GET}?{qs}"
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                url, headers={"Accept": "text/xml", "User-Agent": "efc-force-ana/1.0"}
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            rows: list[dict] = []
            for el in root.iter():
                if el.tag.endswith("DadosHidrometereologicos"):
                    d = {c.tag.split("}")[-1]: (c.text or "").strip() for c in el}
                    rows.append(d)
            return rows
        except Exception as exc:
            last_err = exc
            if attempt < 3:
                wait = min(30.0, 1.6**attempt)
                _log(f"  legacy {code}: {exc}, retry in {wait:.1f}s")
                time.sleep(wait)
                continue
            raise
    assert last_err is not None
    raise last_err


def to_bq_rows(code: str, raw: list[dict], ingested_at: str) -> list[dict]:
    loc = f"estacao_{code}"
    nivel_by_day: dict[date, list[float]] = {}
    vazao_by_day: dict[date, list[float]] = {}
    for x in raw:
        dh = str(x.get("DataHora") or "").strip()
        if not dh:
            continue
        try:
            ts = datetime.strptime(dh[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        d = ts.date()
        if x.get("Nivel"):
            try:
                # ANA telemétrica / HidroInfo Cota_Adotada are in centimetres.
                nivel_by_day.setdefault(d, []).append(float(str(x["Nivel"]).replace(",", ".")) / 100.0)
            except ValueError:
                pass
        if x.get("Vazao"):
            try:
                vazao_by_day.setdefault(d, []).append(float(str(x["Vazao"]).replace(",", ".")))
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
    _log(f"deleted window {start}..{end} for {len(codes)} stations (job {job.job_id})")


def load_rows(client: bigquery.Client, rows: list[dict]) -> None:
    if not rows:
        _log("no rows to load")
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
    _log(f"loaded {len(rows)} rows → {table}")


def verify(client: bigquery.Client) -> None:
    sql = f"""
    SELECT variable, MAX(date) AS max_date, COUNT(*) AS n
    FROM `{PROJECT}.{DATASET}.{TABLE}`
    WHERE source = 'ana'
      AND variable IN ('nivel_m', 'vazao_m3s')
      AND date BETWEEN '2020-01-01' AND '2030-12-31'
    GROUP BY variable
    ORDER BY variable
    """
    for row in client.query(sql).result():
        _log(f"verify: {row.variable} max_date={row.max_date} n={row.n}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--codes", type=str, default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prefer-legacy", action="store_true")
    ap.add_argument("--pause-sec", type=float, default=0.25)
    ap.add_argument("--csv-out", type=str, default="/tmp/ana_nivel_bq_rows.csv")
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or DEFAULT_CODES
    end = date.today()
    start = end - timedelta(days=args.days)
    ingested_at = datetime.now(timezone.utc).isoformat()

    token = ""
    if not args.prefer_legacy:
        try:
            token = hidroinfo_token()
            _log("HidroInfo token OK")
        except Exception as exc:
            _log(f"HidroInfo auth failed ({exc}); will use legacy GET only")

    all_rows: list[dict] = []
    ok = 0
    for i, code in enumerate(codes, 1):
        raw: list[dict] = []
        source = ""
        try:
            if token and not args.prefer_legacy:
                raw = fetch_hidroinfo(code, end, args.days, token)
                source = "hidroinfo"
            if not raw:
                raw = fetch_legacy(code, start, end)
                source = "legacy"
            rows = to_bq_rows(code, raw, ingested_at)
            _log(f"[{i}/{len(codes)}] {code}: {source} raw={len(raw)} bq_rows={len(rows)}")
            all_rows.extend(rows)
            ok += 1
        except Exception as exc:
            _log(f"[{i}/{len(codes)}] {code}: FAIL {exc}")
        time.sleep(max(0.0, float(args.pause_sec)))

    Path(args.csv_out).parent.mkdir(parents=True, exist_ok=True)
    if all_rows:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)
        _log(f"wrote {args.csv_out} ({len(all_rows)} rows, {ok}/{len(codes)} stations)")

    if args.dry_run:
        _log("dry-run: skip BQ")
        return 0 if ok else 1

    client = bigquery.Client(project=PROJECT)
    delete_window(client, codes, start, end)
    load_rows(client, all_rows)
    verify(client)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
