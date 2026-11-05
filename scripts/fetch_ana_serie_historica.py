#!/usr/bin/env python3
"""Download ANA telemetric nível series (monthly chunks) → daily Excel."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.utils import get_column_letter

ANA_GET = "http://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"
CACHE = Path("/tmp/ana_series_cache")
OUT_XLSX = Path("/workspace/EFC_nivel_sensores_completo.xlsx")
ART = Path("/opt/cursor/artifacts")
SRC = Path("/workspace/EFC_nivel_pontos_resumo.xlsx")

START = date(2010, 1, 1)
END = date(2026, 10, 31)
WORKERS = 8
TIMEOUT = 90
RETRIES = 2


def month_starts(start: date, end: date):
    y, m = start.year, start.month
    while True:
        d = date(y, m, 1)
        if d > end:
            break
        yield d
        m += 1
        if m == 13:
            m = 1
            y += 1


def month_end(d: date) -> date:
    if d.month == 12:
        nxt = date(d.year + 1, 1, 1)
    else:
        nxt = date(d.year, d.month + 1, 1)
    from datetime import timedelta
    return nxt - timedelta(days=1)


def cache_path(code: str, ym: date) -> Path:
    return CACHE / f"{code}_{ym.year:04d}{ym.month:02d}.json"


def fetch_month(code: str, ym: date) -> list[dict]:
    cp = cache_path(code, ym)
    if cp.exists():
        return json.loads(cp.read_text())
    a = ym.strftime("%d/%m/%Y")
    b = min(month_end(ym), END).strftime("%d/%m/%Y")
    qs = urllib.parse.urlencode({"codEstacao": code, "dataInicio": a, "dataFim": b})
    url = f"{ANA_GET}?{qs}"
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GeoInfra-ANA/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = r.read()
            if data.startswith(b"<html") or len(data) < 200:
                raise RuntimeError(f"bad response {data[:80]!r}")
            root = ET.fromstring(data)
            rows = []
            for el in root.iter():
                if el.tag.endswith("DadosHidrometereologicos"):
                    d = {c.tag.split("}")[-1]: (c.text or "").strip() for c in el}
                    rows.append(d)
            cp.write_text(json.dumps(rows, ensure_ascii=False))
            return rows
        except Exception as e:
            last_err = e
            time.sleep(1 + attempt * 2)
    # Cache empty after persistent failure so resume can proceed (ANA 504s on some months).
    cp.write_text("[]")
    print(f"  EMPTY_CACHE {code} {ym}: {last_err}", flush=True)
    return []


def to_daily(code: str, raw: list[dict]) -> list[tuple]:
    by: dict[date, list[float]] = {}
    for x in raw:
        if not x.get("DataHora") or not x.get("Nivel"):
            continue
        try:
            ts = datetime.strptime(x["DataHora"][:19].strip(), "%Y-%m-%d %H:%M:%S")
            # ANA telemétrica Nivel em cm → m
            v = float(x["Nivel"]) / 100.0
        except Exception:
            continue
        by.setdefault(ts.date(), []).append(v)
    out = []
    for d, vals in sorted(by.items()):
        out.append((code, d.isoformat(), round(sum(vals) / len(vals), 4), len(vals)))
    return out


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    ana = pd.read_excel(SRC, "ANA_corredor_EFC").copy()
    codes = [str(int(c)) if float(c).is_integer() else str(c) for c in ana["code"]]
    # zero-pad to 8 where needed except short codes like 347006
    codes_fmt = []
    for c in codes:
        codes_fmt.append(c.zfill(8) if len(c) <= 8 else c)
    # map original for join
    code_map = {str(int(r.code)) if float(r.code).is_integer() else str(r.code): r for _, r in ana.iterrows()}

    months = list(month_starts(START, END))
    jobs = [(c, ym) for c in codes_fmt for ym in months]
    print(f"jobs={len(jobs)} stations={len(codes_fmt)} months={len(months)} workers={WORKERS}")

    ok = fail = 0
    daily_rows: list[tuple] = []
    # process station by station to control memory / progress
    for i, code in enumerate(codes_fmt, 1):
        print(f"[{i}/{len(codes_fmt)}] {code}", flush=True)
        station_daily: dict[str, tuple] = {}
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(fetch_month, code, ym): ym for ym in months}
            for fut in as_completed(futs):
                ym = futs[fut]
                try:
                    raw = fut.result()
                    ok += 1
                    for row in to_daily(code, raw):
                        station_daily[row[1]] = row  # date key
                except Exception as e:
                    fail += 1
                    print(f"  FAIL {code} {ym}: {e}", flush=True)
        daily_rows.extend(station_daily[k] for k in sorted(station_daily))
        print(f"  days={len(station_daily)} cumulative_daily={len(daily_rows)} ok={ok} fail={fail}", flush=True)

    # catalog sheet
    cat = pd.DataFrame({
        "codigo": [str(int(c)) if float(c).is_integer() else str(c) for c in ana["code"]],
        "nome": ana["nome"],
        "rio": ana["rio"].fillna("—"),
        "municipio": ana["municipio"].fillna("—"),
        "lat": ana["lat"].round(5),
        "lon": ana["lon"].round(5),
        "km_efc": ana["nearest_efc_km"].round(0).astype(int),
        "dist_eixo_km": ana["dist_efc_km"].round(1),
    })
    # attach period from series
    serie = pd.DataFrame(daily_rows, columns=["codigo", "data", "nivel_m", "n_obs"])
    # normalize codigo without leading zeros mismatch
    def norm(c):
        c = str(c).lstrip("0") or "0"
        return c
    serie["codigo_n"] = serie["codigo"].map(norm)
    cat["codigo_n"] = cat["codigo"].map(norm)
    periods = (
        serie.groupby("codigo_n")
        .agg(data_ini=("data", "min"), data_fim=("data", "max"), n_dias=("data", "count"))
        .reset_index()
    )
    cat = cat.merge(periods, on="codigo_n", how="left").drop(columns=["codigo_n"])
    serie_out = serie.drop(columns=["codigo_n"])
    # use unpadded codigo matching cat
    remap = {str(int(c)).zfill(8) if len(str(int(c))) <= 8 else str(int(c)): str(int(c)) for c in ana["code"]}
    # also handle 347006
    for c in ana["code"]:
        s = str(int(c)) if float(c).is_integer() else str(c)
        remap[s.zfill(8)] = s
        remap[s] = s
    serie_out["codigo"] = serie_out["codigo"].map(lambda x: remap.get(x, x.lstrip("0") or x))

    # write excel
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="243447")
    header_font = Font(color="FFFFFF", bold=True, name="Calibri", size=10)
    cell_font = Font(name="Calibri", size=10)
    thin = Border(
        left=Side(style="thin", color="D9D2C5"),
        right=Side(style="thin", color="D9D2C5"),
        top=Side(style="thin", color="D9D2C5"),
        bottom=Side(style="thin", color="D9D2C5"),
    )
    alt = PatternFill("solid", fgColor="F7F4EF")

    def write_df(ws, df):
        df = df.where(pd.notnull(df), None)
        for r_i, row in enumerate(dataframe_to_rows(df, index=False, header=True), 1):
            for c_i, val in enumerate(row, 1):
                cell = ws.cell(r_i, c_i, val)
                cell.font = header_font if r_i == 1 else cell_font
                if r_i == 1:
                    cell.fill = header_fill
                elif r_i % 2 == 0:
                    cell.fill = alt
                cell.border = thin
                cell.alignment = Alignment(vertical="center")
        ws.freeze_panes = "A2"
        for col in ws.columns:
            letter = get_column_letter(col[0].column)
            maxlen = 0
            for cell in col[:80]:
                maxlen = max(maxlen, len(str(cell.value)) if cell.value is not None else 0)
            ws.column_dimensions[letter].width = min(max(maxlen + 2, 9), 36)

    ws1 = wb.active
    ws1.title = "ANA"
    write_df(ws1, cat)

    ws2 = wb.create_sheet("serie_nivel_diaria")
    write_df(ws2, serie_out)

    # resumo periodo
    resumo = cat[["codigo", "nome", "data_ini", "data_fim", "n_dias"]].copy()
    if resumo["data_ini"].notna().any():
        resumo["periodo_dias"] = (
            pd.to_datetime(resumo["data_fim"]) - pd.to_datetime(resumo["data_ini"])
        ).dt.days + 1
    ws3 = wb.create_sheet("resumo_periodos")
    write_df(ws3, resumo.sort_values("n_dias", ascending=False))

    wb.save(OUT_XLSX)
    ART.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(OUT_XLSX, ART / OUT_XLSX.name)
    print("WROTE", OUT_XLSX, "rows_serie", len(serie_out), "ok", ok, "fail", fail)
    print(resumo.sort_values("n_dias", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main()
