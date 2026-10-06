#!/usr/bin/env python3
"""Ingestão CEMADEN Nova Vida (INMET D0108 / 150553602A) → estacao_D6182.

BNDMET D6182 e INMET/CEMADEN D0108 são o mesmo sítio físico em Parauapebas
(lat/lon ≈ -6.0914, -49.9045). A API BNDMET (I175) para D6182 costuma vir
esparsa (~9 mm/30 d); a série horária CEMADEN é a verdade observada alinhada
ao mapa INMET.

Uso (na raiz ClimaticSystem / cb-src):
  PYTHONPATH=. python scripts/ingest_cemaden_d0108_to_d6182.py
  PYTHONPATH=. python scripts/ingest_cemaden_d0108_to_d6182.py --days 40 --also-alias
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
# Quando o script vive em /workspace/scripts (patch), também aceita cb-src.
for candidate in (ROOT, Path("/tmp/cb-src"), Path.cwd()):
    if (candidate / "gcp" / "bq_load_clima.py").is_file():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        ROOT = candidate
        break

from gcp.bq_load_clima import (  # noqa: E402
    _bq_client,
    delete_clima_series_for_locations,
    load_rows_to_bq,
)

CEMADEN_IDESTACAO = 6798  # Nova Vida
CEMADEN_CODE = "150553602A"
CANONICAL_LOCATION = "estacao_D6182"
ALIAS_LOCATION = "estacao_D0108"
CEMADEN_WS = "https://mapservices.cemaden.gov.br/MapaInterativoWS/resources/"


def fetch_cemaden_daily(idp: int, days: int) -> list[tuple[date, float]]:
    hours = max(24, int(days) * 24 - 1)
    url = f"{CEMADEN_WS}horario/{int(idp)}/{hours}"
    payload = requests.get(url, timeout=90).json()
    out: list[tuple[date, float]] = []
    for d_str, row in zip(payload["datas"], payload["acumulados"]):
        d = datetime.strptime(d_str, "%d/%m/%Y").date()
        mm = float(sum(x or 0 for x in row))
        out.append((d, round(mm, 2)))
    return out


def build_rows(daily: list[tuple[date, float]], location_ids: list[str]) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for loc in location_ids:
        for d, mm in daily:
            vt = datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc).isoformat()
            rows.append(
                {
                    "date": d.isoformat(),
                    "location_id": loc,
                    "variable": "precip_mm",
                    "value": float(mm),
                    "source": "estacoes",
                    "ingested_at": now,
                    "valid_time": vt,
                }
            )
    return rows


def upsert_locations(location_specs: list[tuple[str, float, float]]) -> None:
    client = _bq_client()
    structs = ", ".join(
        f"STRUCT('{lid}' AS location_id, {lat} AS lat, {lon} AS lon, 'estacao' AS source)"
        for lid, lat, lon in location_specs
    )
    sql = f"""
    MERGE `climaticsystem.efc_clima.locations` T
    USING UNNEST([{structs}]) S
    ON T.location_id = S.location_id
    WHEN MATCHED THEN UPDATE SET lat = S.lat, lon = S.lon, source = S.source
    WHEN NOT MATCHED THEN
      INSERT (location_id, lat, lon, source) VALUES (S.location_id, S.lat, S.lon, S.source)
    """
    client.query(sql).result()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument(
        "--also-alias",
        action="store_true",
        default=True,
        help="Também grava série em estacao_D0108 (default: sim)",
    )
    ap.add_argument("--no-alias", action="store_true", help="Só estacao_D6182")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    locs = [CANONICAL_LOCATION]
    if args.also_alias and not args.no_alias:
        locs.append(ALIAS_LOCATION)

    daily = fetch_cemaden_daily(CEMADEN_IDESTACAO, args.days)
    cut = date.today() - timedelta(days=29)
    mm30 = round(sum(v for d, v in daily if d >= cut), 2)
    print(
        f"CEMADEN {CEMADEN_CODE} (id={CEMADEN_IDESTACAO}) → {locs}: "
        f"{len(daily)} dias, soma 30d={mm30} mm"
    )
    rainy = [(d.isoformat(), v) for d, v in daily if v > 0]
    print("dias com chuva:", rainy)

    rows = build_rows(daily, locs)
    if args.dry_run:
        print(f"[dry-run] {len(rows)} linhas; não escreve BQ")
        return 0

    upsert_locations(
        [
            (CANONICAL_LOCATION, -6.0914, -49.9044),
            (ALIAS_LOCATION, -6.0914, -49.9045),
        ]
    )
    deleted = delete_clima_series_for_locations(
        "efc_clima", locs, source="estacoes", dry_run=False
    )
    print(f"removidas {deleted} linhas antigas (source=estacoes)")
    inserted = load_rows_to_bq(rows, "efc_clima", "clima_series", append=True)
    print(f"inseridas {inserted} linhas limpas (1 valor/dia)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
