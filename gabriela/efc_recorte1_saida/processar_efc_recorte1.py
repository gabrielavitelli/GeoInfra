#!/usr/bin/env python3
"""Processa efc_recorte1.zip -> mapa HTML, tabela CSV, XLSX séries (nessa ordem)."""
from __future__ import annotations
import json, os, re, sys, zipfile, shutil
from pathlib import Path
from collections import Counter
from datetime import date

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, mapping
from shapely.ops import unary_union

OUT = Path(os.environ.get("EFC_OUT", "/workspace/gabriela/efc_recorte1_saida"))
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path("/tmp/efc_recorte1_work")
WORK.mkdir(parents=True, exist_ok=True)

CANDIDATES = [
    Path("/home/gabriela/efc_recorte1.zip"),
    Path("/home/gabriela/efc_recorte1"),
    Path("/home/gabriela/gabriela/efc_recorte1.zip"),
    Path("/home/gabriela/dados/efc_recorte1.zip"),
    Path("/home/gabriela/dados/shp/EFC/efc_recorte1.zip"),
    Path("/home/ubuntu/gabriela/efc_recorte1.zip"),
    Path("/home/ubuntu/efc_recorte1.zip"),
    Path.home() / "gabriela" / "efc_recorte1.zip",
    Path.home() / "efc_recorte1.zip",
    Path("/workspace/gabriela/efc_recorte1.zip"),
    Path("/workspace/gabriela/efc_recorte1"),
    Path("/workspace/gabriela/EFC_recorte1.zip"),
    Path("/tmp/efc_recorte1.zip"),
    Path("/tmp/uploads/efc_recorte1.zip"),
]

SEARCH_ROOTS = [
    Path("/home/gabriela"),
    Path("/home/ubuntu/gabriela"),
    Path("/home/ubuntu"),
    Path("/workspace/gabriela"),
    Path("/tmp"),
    Path("/tmp/uploads"),
    Path("/tmp/cursor"),
]


def _is_zip_or_shp(f: Path) -> bool:
    if not f.is_file():
        return False
    suf = f.suffix.lower()
    if suf == ".shp":
        return True
    if suf == ".zip":
        return zipfile.is_zipfile(f)
    try:
        return zipfile.is_zipfile(f)
    except Exception:
        return False


def find_zip() -> Path | None:
    for p in CANDIDATES:
        if p.is_file() and _is_zip_or_shp(p):
            return p
        if p.is_dir():
            for f in p.rglob("*"):
                name = f.name.lower()
                if "recorte1" not in name and "efc_recorte" not in name:
                    continue
                if "saida" in f.as_posix().lower():
                    continue
                # never use the wrong buffer output
                if "efc_recorte_saida" in f.as_posix().lower():
                    continue
                if f.suffix.lower() == ".zip" and zipfile.is_zipfile(f):
                    return f
                if f.suffix.lower() == ".shp":
                    return f
    # recursive fuzzy search (prefer /home/gabriela)
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        try:
            for f in root.rglob("*"):
                if not f.is_file():
                    continue
                posix = f.as_posix().lower()
                if "saida" in posix or "efc_recorte_saida" in posix:
                    continue
                if "python_gabriela" in posix or "site-packages" in posix:
                    continue
                name = f.name.lower()
                if "recorte1" not in name:
                    continue
                if _is_zip_or_shp(f):
                    return f
        except PermissionError:
            continue
    return None


def unzip_shapefile(src: Path) -> Path:
    shp_dir = WORK / "shp"
    if shp_dir.exists():
        shutil.rmtree(shp_dir)
    shp_dir.mkdir(parents=True)
    if src.suffix.lower() == ".shp":
        # copy sidecar files
        for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".qmd", ".qpj"]:
            sib = src.with_suffix(ext)
            if sib.exists():
                shutil.copy2(sib, shp_dir / sib.name)
        return next(shp_dir.glob("*.shp"))
    with zipfile.ZipFile(src) as z:
        z.extractall(shp_dir)
    shps = list(shp_dir.rglob("*.shp"))
    if not shps:
        raise SystemExit(f"Nenhum .shp dentro de {src}")
    # prefer name with recorte
    shps.sort(key=lambda p: (0 if "recorte" in p.name.lower() else 1, len(p.name)))
    return shps[0]


def load_polygon(shp_path: Path):
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    geom = unary_union(gdf.geometry)
    return gdf, geom


def load_stations_catalog() -> pd.DataFrame:
    frames = []
    # BNDMET CSV
    bnd = Path("/tmp/bndmet_estacoes_efc.csv")
    if bnd.exists():
        df = pd.read_csv(bnd)
        df = df.rename(columns={"codEstacao": "station_id", "latitude": "lat", "longitude": "lon"})
        df["source"] = "bndmet"
        frames.append(df[["source", "station_id", "nome", "lat", "lon"]])
    # ANA CSV
    for ana_p in [Path("/tmp/ana_estacoes_efc_merged.csv"), Path("/tmp/ana_estacoes_efc.csv")]:
        if ana_p.exists():
            df = pd.read_csv(ana_p)
            df = df.rename(columns={"codEstacao": "station_id", "latitude": "lat", "longitude": "lon"})
            df["source"] = "ana"
            cols = ["source", "station_id", "nome", "lat", "lon"]
            if "municipio_uf" in df.columns:
                df["municipio"] = df["municipio_uf"]
                cols.append("municipio")
            frames.append(df[cols])
            break
    # ANA telemetria inventory (Norte)
    tele = Path("/tmp/ana_telemetria_norte.json")
    if tele.exists():
        rows = json.load(open(tele))
        df = pd.DataFrame(rows)
        if not df.empty:
            if "source" not in df.columns:
                df["source"] = "ana"
            df["source"] = "ana"
            keep = [c for c in ["source", "station_id", "nome", "lat", "lon", "municipio"] if c in df.columns]
            frames.append(df[keep])
    # live map markers
    sm = Path("/tmp/efc_recorte1_work/stations_from_map.json")
    if sm.exists():
        rows = json.load(open(sm))
        df = pd.DataFrame([
            {
                "source": ("bndmet" if r["source"].upper() in ("BNDMET", "INMET", "ESTACOES") else r["source"].lower()),
                "station_id": r["station_id"],
                "nome": r.get("nome") or r["station_id"],
                "lat": r["lat"],
                "lon": r["lon"],
            }
            for r in rows if r.get("source", "").upper() not in ("KM",)
        ])
        if not df.empty:
            frames.append(df)
    # BigQuery if available
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project="climaticsystem")
        q = """
        SELECT
          LOWER(source) AS source,
          location_id AS station_id,
          COALESCE(name, location_id) AS nome,
          lat, lon,
          CAST(extra AS STRING) AS extra
        FROM `climaticsystem.efc_clima.locations`
        WHERE lat IS NOT NULL AND lon IS NOT NULL
          AND (
            LOWER(source) IN ('uniplu','ana','estacoes','bndmet','inmet')
            OR STARTS_WITH(location_id, 'estacao_')
            OR STARTS_WITH(location_id, 'uniplu')
          )
        """
        bq = client.query(q).to_dataframe()
        # normalize source
        def norm_src(row):
            s = str(row["source"] or "").lower()
            lid = str(row["station_id"] or "")
            if s in ("estacoes", "bndmet", "inmet") or re.match(r"estacao_[A-Za-z]", lid):
                return "bndmet"
            if s == "ana" or re.match(r"estacao_\d", lid):
                return "ana"
            if "uniplu" in s or lid.startswith("uniplu"):
                return "uniplu"
            return s or "other"
        bq["source"] = bq.apply(norm_src, axis=1)
        bq["station_id"] = bq["station_id"].astype(str).str.replace(r"^estacao_", "", regex=True)
        frames.append(bq[["source", "station_id", "nome", "lat", "lon"] + (["extra"] if "extra" in bq.columns else [])])
        print("BQ locations:", len(bq), dict(Counter(bq["source"])))
    except Exception as e:
        print("BQ indisponivel:", e)

    if not frames:
        raise SystemExit("Nenhum catálogo de estações disponível")
    all_st = pd.concat(frames, ignore_index=True, sort=False)
    all_st["station_id"] = all_st["station_id"].astype(str)
    all_st["lat"] = pd.to_numeric(all_st["lat"], errors="coerce")
    all_st["lon"] = pd.to_numeric(all_st["lon"], errors="coerce")
    all_st = all_st.dropna(subset=["lat", "lon"])
    all_st = all_st.drop_duplicates(subset=["source", "station_id"], keep="first")
    return all_st


def filter_inside(df: pd.DataFrame, geom) -> pd.DataFrame:
    mask = df.apply(lambda r: geom.contains(Point(float(r.lon), float(r.lat))) or geom.intersects(Point(float(r.lon), float(r.lat)).buffer(1e-9)), axis=1)
    return df.loc[mask].copy()


def write_map(df: pd.DataFrame, geom, out_html: Path, title: str):
    colors = {"bndmet": "#2563eb", "ana": "#dc2626", "uniplu": "#16a34a", "estacoes": "#2563eb"}
    stations_js = df.fillna("").to_dict(orient="records")
    for s in stations_js:
        s["lat"] = float(s["lat"]); s["lon"] = float(s["lon"])
        s["station_id"] = str(s["station_id"]); s["nome"] = str(s.get("nome") or "")
        s["source"] = str(s["source"])
        if "municipio" in s:
            s["municipio"] = str(s["municipio"])
    counts = dict(Counter(df["source"]))
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {{ --bg:#0b1220; --panel:#111827; --text:#e5e7eb; --muted:#94a3b8; --line:#334155; }}
  * {{ box-sizing:border-box; }}
  html,body {{ margin:0; height:100%; font-family:"IBM Plex Sans","Segoe UI",sans-serif; background:var(--bg); color:var(--text); }}
  #wrap {{ display:grid; grid-template-columns:380px 1fr; height:100%; }}
  #side {{ background:linear-gradient(165deg,#0b1220,#111827 60%,#1e293b); border-right:1px solid var(--line); padding:16px; overflow:auto; }}
  h1 {{ font-size:18px; margin:0 0 6px; }}
  .meta {{ color:var(--muted); font-size:12px; line-height:1.45; margin-bottom:10px; }}
  .counts {{ display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 12px; }}
  .chip {{ font-size:12px; padding:4px 8px; border-radius:6px; background:#1e293b; border:1px solid #334155; }}
  input[type=search] {{ width:100%; padding:8px 10px; border-radius:8px; border:1px solid #334155; background:#0b1220; color:#e5e7eb; margin-bottom:10px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  th,td {{ text-align:left; padding:7px 4px; border-bottom:1px solid #1f2937; }}
  th {{ color:#93c5fd; position:sticky; top:0; background:#111827; }}
  tr:hover {{ background:#1e293b; cursor:pointer; }}
  #map {{ height:100%; }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; }}
  @media (max-width:840px){{ #wrap{{ grid-template-columns:1fr; grid-template-rows:42% 58%; }} }}
</style>
</head>
<body>
<div id="wrap">
  <aside id="side">
    <h1>{title}</h1>
    <div class="meta">Contorno = polígono do shapefile efc_recorte1. Marcadores: Uniplu / ANA / BNDMET dentro da área.</div>
    <div class="counts" id="counts"></div>
    <input id="q" type="search" placeholder="Filtrar código ou nome…"/>
    <table><thead><tr><th>Fonte</th><th>ID</th><th>Nome</th><th>Lat</th><th>Lon</th></tr></thead><tbody id="tbody"></tbody></table>
  </aside>
  <div id="map"></div>
</div>
<script>
const stations = {json.dumps(stations_js, ensure_ascii=False)};
const boundary = {json.dumps(mapping(geom))};
const colors = {json.dumps(colors)};
const map = L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom:18, attribution:'&copy; OpenStreetMap' }}).addTo(map);
const boundLayer = L.geoJSON(boundary, {{ style:{{ color:'#f59e0b', weight:2.5, fillColor:'#f59e0b', fillOpacity:0.08 }} }}).addTo(map);
const markers = {{}};
const group = L.featureGroup();
stations.forEach((s,i) => {{
  const c = colors[s.source] || '#a78bfa';
  const m = L.circleMarker([s.lat, s.lon], {{ radius:7, color:'#0f172a', weight:1, fillColor:c, fillOpacity:0.95 }});
  m.bindPopup(`<b>${{s.source.toUpperCase()}} ${{s.station_id}}</b><br>${{s.nome||''}}<br>lat ${{s.lat.toFixed(5)}}, lon ${{s.lon.toFixed(5)}}`);
  m.addTo(group); markers[i]=m;
}});
group.addTo(map);
map.fitBounds(boundLayer.getBounds().pad(0.08));
const counts = stations.reduce((a,s)=>{{a[s.source]=(a[s.source]||0)+1; return a;}}, {{}});
document.getElementById('counts').innerHTML = Object.entries(counts).map(([k,v])=>`<span class="chip"><span class="dot" style="background:${{colors[k]||'#aaa'}}"></span>${{k}} <b>${{v}}</b></span>`).join('') + `<span class="chip">total <b>${{stations.length}}</b></span>`;
function render(filter='') {{
  const tb=document.getElementById('tbody'); tb.innerHTML='';
  stations.forEach((s,i)=>{{
    const hay=(s.source+' '+s.station_id+' '+s.nome).toLowerCase();
    if(filter && !hay.includes(filter)) return;
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><span class="dot" style="background:${{colors[s.source]||'#aaa'}}"></span>${{s.source}}</td><td>${{s.station_id}}</td><td>${{s.nome||''}}</td><td>${{s.lat.toFixed(4)}}</td><td>${{s.lon.toFixed(4)}}</td>`;
    tr.onclick=()=>{{ map.setView([s.lat,s.lon], 10); markers[i].openPopup(); }};
    tb.appendChild(tr);
  }});
}}
render();
document.getElementById('q').addEventListener('input', e=>render(e.target.value.trim().toLowerCase()));
</script>
</body></html>
"""
    out_html.write_text(html, encoding="utf-8")
    print("Parte1 mapa:", out_html, counts)


def fetch_series_bq(station_ids_by_source: dict[str, list[str]]) -> pd.DataFrame:
    from google.cloud import bigquery
    client = bigquery.Client(project="climaticsystem")
    # Build filters
    clauses = []
    params = []
    # Prefer matching location_id with and without estacao_ prefix
    all_ids = []
    for src, ids in station_ids_by_source.items():
        for i in ids:
            all_ids.append(str(i))
            all_ids.append(f"estacao_{i}")
    all_ids = sorted(set(all_ids))
    if not all_ids:
        return pd.DataFrame()
    q = """
    SELECT
      LOWER(source) AS source,
      location_id,
      CAST(date AS STRING) AS date,
      variable,
      value AS precip_mm
    FROM `climaticsystem.efc_clima.clima_series`
    WHERE variable = 'precip_mm'
      AND location_id IN UNNEST(@ids)
    ORDER BY source, location_id, date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", all_ids)]
    )
    return client.query(q, job_config=job_config).to_dataframe()


def main():
    src = find_zip()
    if not src:
        (OUT / "STATUS.txt").write_text(
            "BLOQUEADO: efc_recorte1.zip não encontrado.\n"
            "Procurado em /home/gabriela, /home/ubuntu, ~/gabriela, /workspace/gabriela, /tmp.\n"
            "Anexe o ZIP ou copie para /workspace/gabriela/efc_recorte1.zip "
            "(ou /home/gabriela/efc_recorte1.zip) e rode este script.\n"
            "NÃO usar /workspace/EFC_recorte_saida (buffer errado).\n",
            encoding="utf-8",
        )
        print("ZIP ausente")
        return 2
    print("Usando:", src)
    shp = unzip_shapefile(src)
    print("Shapefile:", shp)
    gdf, geom = load_polygon(shp)
    gdf.to_file(OUT / "efc_recorte1_poligono.geojson", driver="GeoJSON")
    # copy shp set
    for f in shp.parent.glob(shp.stem + ".*"):
        shutil.copy2(f, OUT / f.name)

    catalog = load_stations_catalog()
    inside = filter_inside(catalog, geom)
    print("Dentro do polígono:", dict(Counter(inside["source"])), "total", len(inside))

    # PART 1
    write_map(inside, geom, OUT / "mapa_estacoes_efc_recorte1.html", "Estações · efc_recorte1")

    # PART 2
    cols = [c for c in ["source", "station_id", "nome", "lat", "lon", "municipio", "extra"] if c in inside.columns]
    tab = inside[cols].sort_values(["source", "station_id"])
    tab.to_csv(OUT / "tabela_estacoes_efc_recorte1.csv", index=False)
    # simple HTML table
    (OUT / "tabela_estacoes_efc_recorte1.html").write_text(
        "<html><head><meta charset='utf-8'><title>Tabela estações efc_recorte1</title>"
        "<style>body{font-family:IBM Plex Sans,Segoe UI,sans-serif;background:#0b1220;color:#e5e7eb;padding:20px}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #334155;padding:6px 8px;font-size:13px}"
        "th{background:#1e293b;color:#93c5fd;position:sticky;top:0}tr:nth-child(even){background:#111827}</style></head><body>"
        f"<h1>Estações dentro de efc_recorte1 ({len(tab)})</h1>"
        + tab.to_html(index=False, escape=True)
        + "</body></html>",
        encoding="utf-8",
    )
    print("Parte2 tabela:", OUT / "tabela_estacoes_efc_recorte1.csv")

    # PART 3
    xlsx = OUT / "dados_precip_efc_recorte1.xlsx"
    by_src = {s: tab.loc[tab.source == s, "station_id"].astype(str).tolist() for s in tab["source"].unique()}
    series = pd.DataFrame()
    try:
        series = fetch_series_bq(by_src)
        print("Séries BQ rows:", len(series))
    except Exception as e:
        print("Séries BQ falhou:", e)
        series = pd.DataFrame(columns=["source", "location_id", "date", "variable", "precip_mm"])

    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        tab.to_excel(w, sheet_name="catalogo_estacoes", index=False)
        if len(series) > 900_000:
            # split by source
            for src, g in series.groupby(series["source"]):
                name = f"serie_{src}"[:31]
                g.to_excel(w, sheet_name=name, index=False)
        else:
            series.to_excel(w, sheet_name="serie_precip_mm", index=False)
        meta = pd.DataFrame([
            {"chave": "shapefile", "valor": str(src)},
            {"chave": "poligono_shp", "valor": str(shp)},
            {"chave": "gerado_em", "valor": date.today().isoformat()},
            {"chave": "n_estacoes", "valor": len(tab)},
            {"chave": "counts", "valor": json.dumps(dict(Counter(tab["source"])), ensure_ascii=False)},
            {"chave": "n_serie_rows", "valor": len(series)},
        ])
        meta.to_excel(w, sheet_name="meta", index=False)
    print("Parte3 xlsx:", xlsx)

    meta_out = {
        "shapefile": str(src),
        "counts": dict(Counter(tab["source"])),
        "total": int(len(tab)),
        "mapa": str(OUT / "mapa_estacoes_efc_recorte1.html"),
        "tabela_csv": str(OUT / "tabela_estacoes_efc_recorte1.csv"),
        "tabela_html": str(OUT / "tabela_estacoes_efc_recorte1.html"),
        "xlsx": str(xlsx),
        "n_serie_rows": int(len(series)),
    }
    (OUT / "README_meta.json").write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "STATUS.txt").write_text("OK — partes 1, 2 e 3 geradas.\n" + json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
    # mirror to /tmp
    tmp = Path("/tmp/efc_recorte1_saida")
    tmp.mkdir(exist_ok=True)
    for f in OUT.iterdir():
        if f.is_file():
            shutil.copy2(f, tmp / f.name)
    print(json.dumps(meta_out, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
