"""
Lógica compartilhada do Monitor EFC — sem dependência de Streamlit.
Usado por app_monitor_efc.py e por componentes de UI.
"""
from pathlib import Path
import os
import re
import zipfile
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import xarray as xr

try:
    import db_dtypes  # noqa: F401 — BigQuery query.to_dataframe() (DATE, NUMERIC, …)
except ImportError:
    pass

DIR = Path(__file__).resolve().parent
DATA_DIR = DIR / "data"
NC_PATH = DATA_DIR / "ecmwf_efc_last7d.nc"
GRAPHCAST_NC_PATH = DATA_DIR / "graphcast_efc_forecast.nc"
KMZ_PATH = DATA_DIR / "shapefile" / "Expansão EFC.kmz"
BNDMET_ESTACOES_CSV = DATA_DIR / "bndmet_estacoes_efc.csv"
BNDMET_DADOS_CSV = DATA_DIR / "bndmet_efc_ultimos_3dias.csv"
ANA_ESTACOES_CSV = DATA_DIR / "ana_estacoes_efc.csv"
ANA_ADOTADA_CSV = DATA_DIR / "ana_telemetrica_adotada_efc.csv"
ANA_DETALHADA_CSV = DATA_DIR / "ana_telemetrica_detalhada_efc.csv"


def _normalize_bndmet_dados_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Alinha nomes de colunas do CSV BNDMET (case-insensitive) ao esperado pelo monitor."""
    if df is None or df.empty:
        return df
    mapping = {}
    for c in df.columns:
        key = str(c).strip().lower().replace(" ", "")
        if key == "datahora":
            mapping[c] = "dataHora"
        elif key in ("codestacao", "codigoestacao"):
            mapping[c] = "codEstacao"
        elif key == "codfenomeno":
            mapping[c] = "codFenomeno"
        elif key == "valor":
            mapping[c] = "valor"
    return df.rename(columns=mapping) if mapping else df
MERGE_RAILWAY_JSON_GLOB = "merge_efc_railway*.json"
LAT_MIN_EFC, LAT_MAX_EFC = -7.5, -1.5
# Extensão operacional da ferrovia EFC (km ao longo do corredor; shapefile ~915 km).
EFC_RAILWAY_KM_MAX = 920
FENOMENO_LABELS = {"I175": "Precipitação (mm)", "I101": "Temperatura (°C)", "I113": "Direção do vento (°)", "I111": "Velocidade do vento (m/s)"}
ANA_LABELS = {
    "precip_mm": "Precipitação adotada (mm)",
    "precip_mm_adotado": "Precipitação adotada (mm)",
    "precip_mm_bruto": "Precipitação bruta (mm)",
    "nivel_m": "Nível adotado (m)",
    "nivel_m_adotado": "Nível adotado (m)",
    "nivel_m_bruto": "Nível bruto (m)",
    "vazao_m3s": "Vazão adotada (m3/s)",
    "vazao_m3s_adotado": "Vazão adotada (m3/s)",
    "vazao_m3s_bruto": "Vazão bruta (m3/s)",
}
LON_MIN_EFC, LON_MAX_EFC = -50.5, -43.0
THRESHOLD_24H_MM = 49.0
THRESHOLD_120H_MM = 250.0  # acumulado móvel 120h (5 dias) — limiar operacional
THRESHOLD_7D_MM = 150.0  # acumulado móvel 7 dias civis
THRESHOLD_30D_MM = 465.0  # acumulado móvel 30 dias civis
THRESHOLD_120H_WARNING_MM = 200.0  # entre isto e 250 = "quase ultrapassando" (amarelo)
VARIABLE_PRECIP_7D_MM = "precip_7d_mm"
VARIABLE_PRECIP_30D_MM = "precip_30d_mm"


def limiar_label_24h(*, lang_pt: bool = True) -> str:
    return f"24 h > {THRESHOLD_24H_MM:.0f} mm"


def limiar_label_5d(*, lang_pt: bool = True) -> str:
    return f"5 d > {THRESHOLD_120H_MM:.0f} mm"


def limiar_label_7d(*, lang_pt: bool = True) -> str:
    return f"7 d > {THRESHOLD_7D_MM:.0f} mm"


def limiar_label_30d(*, lang_pt: bool = True) -> str:
    return f"30 d > {THRESHOLD_30D_MM:.0f} mm"


def limiar_kind_from_label(label: str) -> str:
    """'24h' | '5d' | '7d' | '30d' | 'raijin' | 'raijin_tp' | 'raijin_fp' a partir do limiar."""
    s = str(label or "").lower()
    if "raijin" in s:
        # "Raijin TP" / "Raijin FP" (outcome verificado); senão pred=1 pendente.
        if re.search(r"\btp\b", s) or "true positive" in s:
            return "raijin_tp"
        if re.search(r"\bfp\b", s) or "false positive" in s:
            return "raijin_fp"
        return "raijin"
    if "30" in s:
        return "30d"
    if "7" in s and "d" in s:
        return "7d"
    if "5" in s and "d" in s:
        return "5d"
    if "120" in s:
        return "5d"
    return "24h"


def is_raijin_limiar_kind(kind: str) -> bool:
    """True para qualquer kind derivado de alerta Raijin (incl. TP/FP)."""
    k = str(kind or "")
    return k == "raijin" or k.startswith("raijin_")
# Camada de suscetibilidade (GeoTIFF) para os mapas 24h/120h
SUSCETIBILIDADE_TIF = DATA_DIR / "indice_suscetibilidade" / "mapa_suscetibilidade_rf (1).tif"
# Basemap: imagem de alta resolução (~1–2 m, Esri World Imagery). OSM removido.
TILES_SATELLITE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
# Ortofoto (zoom alto): Esri World Imagery Clarity — tiles distintos do World Imagery padrão.
TILES_ORTHO = os.environ.get(
    "EFC_MAP_ORTHO_TILES",
    "https://clarity.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
)
# Alias legado (código antigo / scripts); não expor no LayerControl.
TILES_PADRAO = TILES_SATELLITE
ATTR_ESRI = "Esri · Maxar/Airbus (~2 m)"
ATTR_ORTHO = "Esri Clarity · orthophoto"
ATTR_OSM = ATTR_ESRI
# Esri não entrega tiles acima de ~z17; sem max_native_zoom o Leaflet pede z18+ → mapa branco.
EFC_MAP_MAX_ZOOM = int(os.environ.get("EFC_MAP_MAX_ZOOM", "18"))
EFC_MAP_SATELLITE_MAX_NATIVE_ZOOM = int(os.environ.get("EFC_MAP_SATELLITE_MAX_NATIVE_ZOOM", "17"))
# Terrarium DEM (AWS) para basemap 3D MapLibre.
EFC_MAP_TERRAIN_TILES = os.environ.get(
    "EFC_MAP_TERRAIN_TILES",
    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
)
EFC_BOLETIM_IMAGERY_GCS = os.environ.get(
    "EFC_BOLETIM_IMAGERY_GCS",
    "gs://efc-clima-data/monitor/boletim-imagery",
)


def _suscetibilidade_map_opacity() -> float:
    """Opacidade Folium da camada Suscetibilidade (0–1). Padrão ~55% na faixa do corredor."""
    try:
        return float(os.environ.get("SUSCETIBILIDADE_MAP_OPACITY", "0.55"))
    except (TypeError, ValueError):
        return 0.55


def _suscetibilidade_buffer_m() -> float:
    """Buffer da faixa colorida em torno da ferrovia (metros de cada lado). Padrão 8 km."""
    try:
        return max(50.0, min(15000.0, float(os.environ.get("SUSCETIBILIDADE_BUFFER_M", "8000"))))
    except (TypeError, ValueError):
        return 8000.0


def _add_folium_basemap_layers(m, *, satellite_visible: bool = True) -> None:
    """Basemap satélite alta resolução (~2 m). Sem OpenStreetMap."""
    import folium

    _ = satellite_visible  # sempre satélite; parâmetro mantido por compatibilidade
    folium.TileLayer(
        tiles=TILES_SATELLITE,
        attr=ATTR_ESRI,
        name="2m",
        overlay=False,
        control=False,  # controle compacto custom no parent (não Folium LayerControl)
        show=True,
        max_zoom=EFC_MAP_SATELLITE_MAX_NATIVE_ZOOM,
        max_native_zoom=EFC_MAP_SATELLITE_MAX_NATIVE_ZOOM,
        no_wrap=True,
    ).add_to(m)


def today_operational_brazil():
    """
    Data civil em America/Sao_Paulo.
    Usar em ingestão diária (MERGE CPTEC, BNDMET, S2S) para alinhar ao dia operacional no Brasil;
    no Cloud Run, date.today() / UTC pode estar um dia à frente ou atrás perto da meia-noite.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def _next_pipeline_run_local_datetime(cron_utc: str | None = None):
    """
    Próximo horário de execução do pipeline diário (America/Sao_Paulo, timezone-aware).

    O cron do Cloud Scheduler é interpretado em UTC (padrão ``0 12 * * *`` = 09:00 BRT).
    """
    import os
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    cron = (
        cron_utc
        or os.environ.get("PIPELINE_SCHEDULE_CRON")
        or os.environ.get("PIPELINE_SCHEDULE")
        or "0 12 * * *"
    ).strip()
    parts = cron.split()
    if len(parts) != 5:
        parts = "0 12 * * *".split()
    minute, hour = int(parts[0]), int(parts[1])

    utc = ZoneInfo("UTC")
    local = ZoneInfo("America/Sao_Paulo")
    now_utc = datetime.now(utc)
    run_utc = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    run_local = run_utc.astimezone(local)
    now_local = now_utc.astimezone(local)
    if now_local >= run_local:
        run_local = run_local + timedelta(days=1)
    return run_local


def next_pipeline_run_local_str(cron_utc: str | None = None) -> str:
    """Próxima execução do pipeline — texto ``dd-mm-YYYY HH:MM`` (horário de Brasília)."""
    return _next_pipeline_run_local_datetime(cron_utc).strftime("%d-%m-%Y %H:%M")


def next_pipeline_run_iso(cron_utc: str | None = None) -> str:
    """Mesmo instante que :func:`next_pipeline_run_local_str`, em ISO 8601 para o front-end."""
    return _next_pipeline_run_local_datetime(cron_utc).isoformat()


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return 2 * R * np.arcsin(np.minimum(1.0, np.sqrt(a)))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em km entre dois pontos (graus decimais)."""
    return float(_haversine_km(lat1, lon1, lat2, lon2))


import re as _re

_ESTACA_RE = _re.compile(r"^(\d+)\+(\d+),(\d+)$")


def _parse_estaca_name(name: str):
    """Converte '599+200,000' -> 599.2 (km). Retorna float ou None."""
    m = _ESTACA_RE.match(name.strip())
    if not m:
        return None
    km_int = int(m.group(1))
    metros = int(m.group(2))
    return km_int + metros / 1000.0


def _parse_kml_from_bytes(kml_bytes: bytes):
    """Extrai LineStrings do conteúdo KML (bytes). Retorna lista de listas [lat, lon]."""
    root = ET.fromstring(kml_bytes)
    lines = []
    for pm in root.iter():
        if "Placemark" not in pm.tag and "Document" not in pm.tag and "Folder" not in pm.tag:
            continue
        for child in pm:
            tag_lower = (child.tag or "").lower()
            if "polygon" in tag_lower or "linearring" in tag_lower or "outerboundary" in tag_lower:
                continue
            if "linestring" in tag_lower or "line" in tag_lower:
                for coords_elem in child.iter():
                    if "coordinates" not in (coords_elem.tag or "").lower():
                        continue
                    text = (coords_elem.text or "").strip()
                    if not text:
                        break
                    points = []
                    for part in text.replace("\n", " ").replace("\t", " ").split():
                        part = part.strip()
                        if not part:
                            continue
                        vals = part.split(",")
                        if len(vals) >= 2:
                            try:
                                lon_f, lat_f = float(vals[0]), float(vals[1])
                                points.append([lat_f, lon_f])
                            except ValueError:
                                continue
                        if len(points) >= 2:
                            lines.append(points)
                    break
    if not lines:
        for coords_elem in root.iter():
            if not (coords_elem.tag.endswith("coordinates") or coords_elem.tag == "coordinates"):
                continue
            text = (coords_elem.text or "").strip()
            if not text:
                continue
            points = []
            for part in text.replace("\n", " ").replace("\t", " ").split():
                part = part.strip()
                if not part:
                    continue
                vals = part.split(",")
                if len(vals) >= 2:
                    try:
                        lon_f, lat_f = float(vals[0]), float(vals[1])
                        points.append([lat_f, lon_f])
                    except ValueError:
                        continue
            if len(points) >= 2:
                lines.append(points)
    def _is_closed(pts):
        if len(pts) < 4:
            return False
        return _haversine_km(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1]) < 0.5
    lines = [ln for ln in lines if not _is_closed(ln)]
    return lines


def _parse_kml_estacas(kml_bytes: bytes):
    """
    Extrai Point Placemarks com nome de estaca (ex: '599+200,000') do KML.
    Retorna lista de (km_oficial, lat, lon) ordenada por km, sem duplicatas.
    """
    root = ET.fromstring(kml_bytes)
    ns = ""
    tag0 = root.tag
    if "}" in tag0:
        ns = tag0[: tag0.index("}") + 1]

    estacas_raw: list[tuple[float, float, float]] = []
    for pm in root.iter(f"{ns}Placemark"):
        name_el = pm.find(f"{ns}name")
        if name_el is None or name_el.text is None:
            continue
        km_val = _parse_estaca_name(name_el.text)
        if km_val is None:
            continue
        point_el = pm.find(f"{ns}Point")
        if point_el is None:
            continue
        coords_el = point_el.find(f"{ns}coordinates")
        if coords_el is None or coords_el.text is None:
            continue
        parts = coords_el.text.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            lon_f, lat_f = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        estacas_raw.append((km_val, lat_f, lon_f))

    # Deduplicar: manter a primeira ocorrência de cada km arredondado a 0.1
    seen: set[float] = set()
    unique: list[tuple[float, float, float]] = []
    for km, lat, lon in sorted(estacas_raw, key=lambda x: x[0]):
        key = round(km, 1)
        if key in seen:
            continue
        seen.add(key)
        unique.append((km, lat, lon))
    return unique


def _parse_shp_lines(shp_path: Path):
    """Lê Shapefile de linhas e retorna lista de linhas [[lat, lon], ...] (mesmo formato que _parse_kmz_lines_only)."""
    if not shp_path.exists():
        return []
    try:
        import shapefile  # pyshp
    except ImportError:
        return []
    lines = []
    try:
        with shapefile.Reader(str(shp_path)) as sf:
            for shape in sf.shapes():
                if shape.shapeType not in (3, 13):  # POLYLINE, POLYLINEZ
                    continue
                parts = list(shape.parts) + [len(shape.points)]
                for i in range(len(parts) - 1):
                    pts = shape.points[parts[i] : parts[i + 1]]
                    if len(pts) < 2:
                        continue
                    # Shapefile: (x, y) = (lon, lat) -> nosso formato [lat, lon]
                    line = [[float(p[1]), float(p[0])] for p in pts]
                    lines.append(line)
    except Exception:
        return []
    return lines


def _parse_kmz_lines_and_estacas(kmz_path: Path):
    """Aceita .kmz (zip com .kml) ou .kml. Retorna (lines, estacas) em uma única passagem."""
    if not kmz_path.exists():
        return [], []
    kml_bytes = None
    try:
        if str(kmz_path).lower().endswith(".kml"):
            kml_bytes = kmz_path.read_bytes()
        else:
            with zipfile.ZipFile(kmz_path, "r") as z:
                for name in z.namelist():
                    if name.lower().endswith(".kml"):
                        kml_bytes = z.read(name)
                        break
    except Exception:
        pass
    if kml_bytes is None:
        try:
            kml_bytes = kmz_path.read_bytes()
        except Exception:
            return [], []

    # Usar iterparse para não carregar toda a árvore em memória
    import io
    lines: list[list[list[float]]] = []
    estacas_raw: list[tuple[float, float, float]] = []

    # Buffer para Placemark corrente
    in_pm = False
    pm_name = None
    pm_has_point = False
    pm_point_coords = None
    pm_linestrings: list[list[list[float]]] = []

    for event, elem in ET.iterparse(io.BytesIO(kml_bytes), events=("start", "end")):
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        if event == "start":
            if tag == "Placemark":
                in_pm = True
                pm_name = None
                pm_has_point = False
                pm_point_coords = None
                pm_linestrings = []
                continue
            if in_pm and tag == "Point":
                pm_has_point = True
                continue

        if event == "end" and tag == "Placemark":
            # Processar o Placemark completo
            if pm_linestrings:
                lines.extend(pm_linestrings)
            if pm_name is not None and pm_has_point and pm_point_coords is not None:
                km_val = _parse_estaca_name(pm_name)
                if km_val is not None:
                    estacas_raw.append((km_val, pm_point_coords[0], pm_point_coords[1]))
            in_pm = False
            elem.clear()
            continue

        if not in_pm:
            if event == "end":
                elem.clear()
            continue

        if event == "end":
            if tag == "name" and pm_name is None:
                pm_name = (elem.text or "").strip()
            elif tag == "Point":
                pm_has_point = True
            elif tag == "coordinates":
                text = (elem.text or "").strip()
                if not text:
                    continue
                parent_is_point = pm_has_point and pm_point_coords is None
                parts_all = text.replace("\n", " ").replace("\t", " ").split()
                if len(parts_all) == 1 and parent_is_point:
                    vals = parts_all[0].split(",")
                    if len(vals) >= 2:
                        try:
                            pm_point_coords = (float(vals[1]), float(vals[0]))
                        except ValueError:
                            pass
                elif len(parts_all) >= 2:
                    pts = []
                    for p in parts_all:
                        vals = p.split(",")
                        if len(vals) >= 2:
                            try:
                                pts.append([float(vals[1]), float(vals[0])])
                            except ValueError:
                                continue
                    if len(pts) >= 2:
                        pm_linestrings.append(pts)
            elif tag == "LineString":
                pass  # coordinates already handled

    # Filtrar polígonos fechados
    def _is_closed(pts):
        if len(pts) < 4:
            return False
        return _haversine_km(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1]) < 0.5
    lines = [ln for ln in lines if not _is_closed(ln)]

    # Deduplicar estacas
    seen: set[float] = set()
    estacas: list[tuple[float, float, float]] = []
    for km, lat, lon in sorted(estacas_raw, key=lambda x: x[0]):
        key = round(km, 1)
        if key in seen:
            continue
        seen.add(key)
        estacas.append((km, lat, lon))

    return lines, estacas


def _line_length_km(points):
    if len(points) < 2:
        return 0.0
    return sum(
        _haversine_km(p[i][0], p[i][1], p[i + 1][0], p[i + 1][1])
        for p in [points] for i in range(len(p) - 1)
    )


def _is_efc_corridor_line(line) -> bool:
    """Descarta artefatos do SHP (ex.: vértices em ±85°/±180°) fora do corredor EFC."""
    if not line or len(line) < 2:
        return False
    pad_lat, pad_lon = 1.5, 2.0
    in_corridor = 0
    for lat, lon in line:
        if abs(lat) >= 84.0 or abs(lon) >= 179.0:
            return False
        if (
            LAT_MIN_EFC - pad_lat <= float(lat) <= LAT_MAX_EFC + pad_lat
            and LON_MIN_EFC - pad_lon <= float(lon) <= LON_MAX_EFC + pad_lon
        ):
            in_corridor += 1
    return in_corridor >= max(1, len(line) // 4)


def _filter_efc_railway_lines(lines: list) -> list:
    """Mantém só trechos da ferrovia EFC (ignora polylines espúrias no shapefile)."""
    if not lines:
        return []
    good = [ln for ln in lines if _is_efc_corridor_line(ln)]
    if good:
        return good
    # Não cair no maior LineString: no SHP isso é artefato global (±85°/±180°).
    return []


def _line_cumulative_km_and_positions(points, interval_km=100):
    if len(points) < 2:
        return np.array([0.0]), []
    pts = np.array(points)
    n = len(pts)
    seg_km = np.zeros(n)
    for i in range(n - 1):
        seg_km[i + 1] = _haversine_km(pts[i, 0], pts[i, 1], pts[i + 1, 0], pts[i + 1, 1])
    cumulative_km = np.cumsum(seg_km)
    markers = []
    next_km = interval_km
    for i in range(1, n):
        while next_km <= cumulative_km[i]:
            t = (next_km - cumulative_km[i - 1]) / (cumulative_km[i] - cumulative_km[i - 1] + 1e-9)
            t = np.clip(t, 0, 1)
            lat_m = pts[i - 1, 0] + t * (pts[i, 0] - pts[i - 1, 0])
            lon_m = pts[i - 1, 1] + t * (pts[i, 1] - pts[i - 1, 1])
            markers.append((int(round(next_km)), float(lat_m), float(lon_m)))
            next_km += interval_km
    return cumulative_km, markers


def _closest_km_on_line(points, cumulative_km, lat_q, lon_q):
    if len(points) < 2 or len(cumulative_km) < 2:
        return None
    pts = np.array(points)
    best_km, best_d = None, np.inf
    for i in range(len(pts) - 1):
        lat_a, lon_a = pts[i, 0], pts[i, 1]
        lat_b, lon_b = pts[i + 1, 0], pts[i + 1, 1]
        dlat, dlon = lat_b - lat_a, (lon_b - lon_a) * np.cos(np.radians(lat_a))
        denom = dlat * dlat + dlon * dlon + 1e-20
        t = ((lat_q - lat_a) * dlat + (lon_q - lon_a) * dlon) / denom
        t = float(np.clip(t, 0, 1))
        lat_proj = lat_a + t * (lat_b - lat_a)
        lon_proj = lon_a + t * (lon_b - lon_a)
        d = _haversine_km(lat_q, lon_q, lat_proj, lon_proj)
        if d < best_d:
            best_d = d
            best_km = cumulative_km[i] + t * (cumulative_km[i + 1] - cumulative_km[i])
    return float(best_km) if best_km is not None else None


def position_at_km(km_float: float, main_line, cumulative_km) -> tuple[float, float] | None:
    """Retorna (lat, lon) na ferrovia no quilômetro dado. main_line = lista de [lat, lon]; cumulative_km = array de km cumulativos por vértice."""
    if main_line is None or cumulative_km is None or len(main_line) < 2 or len(cumulative_km) < 2:
        return None
    km = float(km_float)
    cum = np.asarray(cumulative_km)
    if km < cum[0] or km > cum[-1]:
        return None
    i = np.searchsorted(cum, km, side="right") - 1
    if i < 0 or i >= len(main_line) - 1:
        return None
    seg_len = cum[i + 1] - cum[i]
    t = (km - cum[i]) / (seg_len + 1e-20)
    t = float(np.clip(t, 0, 1))
    lat = main_line[i][0] + t * (main_line[i + 1][0] - main_line[i][0])
    lon = main_line[i][1] + t * (main_line[i + 1][1] - main_line[i][1])
    return (float(lat), float(lon))


# Shapefile (mais leve que KMZ) — gerado por scripts/kmz_to_shp.py
RAILWAY_SHP_PATH = DATA_DIR / "shapefile" / "ferrovia_efc" / "ferrovia_efc.shp"
# KML completo da ferrovia EFC — caminho Windows: data\EFC.kmz.kml
EFC_FULL_KML_PATH = DATA_DIR / "EFC.kmz.kml"
# Ordem de busca: SHP primeiro (leve), depois KMZ/KML
RAILWAY_GEOM_CANDIDATES = [
    RAILWAY_SHP_PATH,
    EFC_FULL_KML_PATH,
    DATA_DIR / "EFC.kmz",
    DATA_DIR / "EFC.kml",
    KMZ_PATH,
    DATA_DIR / "shapefile" / "Expansão EFC.kmz",
]


def get_railway_geometry_path(kmz_path: Path = None) -> Path | None:
    """Retorna o Path do arquivo de geometria que será usado (para exibir no app)."""
    if kmz_path is not None and kmz_path.exists():
        return kmz_path
    for p in RAILWAY_GEOM_CANDIDATES:
        if p.exists():
            return p
    return KMZ_PATH if KMZ_PATH.exists() else None


def _minimal_railway_geometry():
    """Geometria mínima (bbox EFC) para modo leve — não lê KMZ. Retorna (main_line, cum_km, [], all_lines)."""
    import os
    if not os.environ.get("LIGHT_GEOM"):
        return None
    main = [[LAT_MIN_EFC, LON_MIN_EFC], [LAT_MAX_EFC, LON_MAX_EFC]]
    dist = _haversine_km(LAT_MIN_EFC, LON_MIN_EFC, LAT_MAX_EFC, LON_MAX_EFC)
    cum_km = np.array([0.0, dist])
    return main, cum_km, [], [main]


def _assign_official_km(main_line, estacas):
    """
    Constrói cumulative_km oficial para cada vértice da main_line a partir
    das estacas [(km_oficial, lat, lon), ...].
    Método: projeta cada estaca na LineString (haversine cum_km), obtém pares
    (haversine_km, oficial_km), e interpola linearmente para todos os vértices.
    """
    if not estacas or len(main_line) < 2:
        return None, None

    pts = np.array(main_line)
    n = len(pts)

    # 1) Cumulative haversine (referência geométrica)
    seg_km = np.zeros(n)
    for i in range(n - 1):
        seg_km[i + 1] = _haversine_km(pts[i, 0], pts[i, 1], pts[i + 1, 0], pts[i + 1, 1])
    cum_hav = np.cumsum(seg_km)

    # 2) Para cada estaca, encontrar posição haversine na linha (busca local otimizada)
    # Pré-computar centroid de cada segmento para busca rápida
    seg_lat = (pts[:-1, 0] + pts[1:, 0]) * 0.5
    seg_lon = (pts[:-1, 1] + pts[1:, 1]) * 0.5

    hav_at_est = []
    km_at_est = []
    for km_of, lat_e, lon_e in estacas:
        # Busca rápida: distância² ao centroid de cada segmento
        dlat_c = seg_lat - lat_e
        dlon_c = (seg_lon - lon_e) * np.cos(np.radians(lat_e))
        dist2_c = dlat_c * dlat_c + dlon_c * dlon_c
        # Checar os 10 segmentos mais próximos
        candidates = np.argsort(dist2_c)[:10]
        best_hav, best_d = None, np.inf
        for i in candidates:
            lat_a, lon_a = pts[i, 0], pts[i, 1]
            lat_b, lon_b = pts[i + 1, 0], pts[i + 1, 1]
            dlat = lat_b - lat_a
            dlon = (lon_b - lon_a) * np.cos(np.radians(lat_a))
            denom = dlat * dlat + dlon * dlon + 1e-20
            t = ((lat_e - lat_a) * dlat + (lon_e - lon_a) * dlon) / denom
            t = float(np.clip(t, 0, 1))
            lat_p = lat_a + t * (lat_b - lat_a)
            lon_p = lon_a + t * (lon_b - lon_a)
            d = _haversine_km(lat_e, lon_e, lat_p, lon_p)
            if d < best_d:
                best_d = d
                best_hav = cum_hav[i] + t * (cum_hav[i + 1] - cum_hav[i])
        if best_hav is not None and best_d < 5.0:
            hav_at_est.append(best_hav)
            km_at_est.append(km_of)

    if len(hav_at_est) < 2:
        return None, None

    hav_at_est = np.array(hav_at_est)
    km_at_est = np.array(km_at_est)

    # Garantir monotonicidade nos pares (hav, oficial) — ordenar por hav
    order = np.argsort(hav_at_est)
    hav_at_est = hav_at_est[order]
    km_at_est = km_at_est[order]

    # 3) Interpolar km oficial para cada vértice da main_line
    cum_official = np.interp(cum_hav, hav_at_est, km_at_est)

    # 4) Markers em múltiplas granularidades para zoom progressivo
    def _interp_markers(interval):
        out = []
        km_s = int(np.ceil(cum_official[0] / interval)) * interval
        km_e = int(np.floor(cum_official[-1] / interval)) * interval
        for tkm in range(km_s, km_e + 1, interval):
            ix = np.searchsorted(cum_official, tkm, side="right") - 1
            if ix < 0 or ix >= n - 1:
                continue
            sg = cum_official[ix + 1] - cum_official[ix]
            if sg < 1e-6:
                continue
            tt = float(np.clip((tkm - cum_official[ix]) / sg, 0, 1))
            lat_m = pts[ix, 0] + tt * (pts[ix + 1, 0] - pts[ix, 0])
            lon_m = pts[ix, 1] + tt * (pts[ix + 1, 1] - pts[ix, 1])
            out.append((tkm, float(lat_m), float(lon_m)))
        return out

    markers_100 = _interp_markers(100)
    markers_50 = _interp_markers(50)
    markers_10 = _interp_markers(10)
    markers_all = {"100": markers_100, "50": markers_50, "10": markers_10}

    return cum_official, markers_all


def get_railway_data(kmz_path: Path = None):
    """Retorna (main_line, cum_km, markers_dict, all_lines).
    markers_dict = {"100": [...], "50": [...], "10": [...]}  com (km, lat, lon).
    Usa km oficial das estacas do KML quando disponível."""
    path = get_railway_geometry_path(kmz_path)
    if path is None or not path.exists():
        return None, None, None, []
    estacas = []
    if path.suffix.lower() == ".shp":
        lines = _parse_shp_lines(path)
    else:
        lines, estacas = _parse_kmz_lines_and_estacas(path)
    lines = _filter_efc_railway_lines(lines)
    if not lines:
        return None, None, None, []
    main = max(lines, key=_line_length_km)
    if len(main) >= 2 and main[0][0] < main[-1][0]:
        main = list(reversed(main))
        lines = [list(reversed(ln)) if len(ln) >= 2 else ln for ln in lines]

    if estacas:
        cum_km, markers = _assign_official_km(main, estacas)
        if cum_km is not None:
            return main, cum_km, markers, lines

    cum_km, markers_flat = _line_cumulative_km_and_positions(main, interval_km=100)
    markers = {"100": markers_flat, "50": markers_flat, "10": markers_flat}
    return main, cum_km, markers, lines


def get_era5_available_dates(nc_path: Path = None):
    """Retorna lista de datas (pd.Timestamp) disponíveis no NetCDF ERA5, sem carregar a grade (leve)."""
    path = nc_path or NC_PATH
    if not path.exists():
        return []
    try:
        ds = xr.open_dataset(path)
        time_coord = ds.coords.get("time") or ds.coords.get("valid_time")
        if time_coord is None:
            for c in ds.coords:
                if "time" in c.lower():
                    time_coord = ds.coords[c]
                    break
        if time_coord is None:
            ds.close()
            return []
        times = time_coord.values
        ds.close()
        if np.isscalar(times):
            return [pd.Timestamp(times)]
        return [pd.Timestamp(t) for t in times]
    except Exception:
        return []


def load_era5(nc_path: Path = None):
    """Carrega ERA5 e retorna (daily_24h, accum_120h, times, lat, lon) ou (None,...) se falhar."""
    path = nc_path or NC_PATH
    if not path.exists():
        return None, None, None, None, None
    try:
        ds = xr.open_dataset(path)
        precip = ds["precip"]
        if "latitude" not in precip.dims:
            lat_c = [c for c in precip.coords if "lat" in c.lower()][0]
            lon_c = [c for c in precip.coords if "lon" in c.lower()][0]
            precip = precip.rename({lat_c: "latitude", lon_c: "longitude"})
        daily_24h = precip
        accum_120h = precip.rolling(time=5, min_periods=1).sum()  # 5 dias = 120h
        times = precip.time.values
        lat = np.atleast_1d(precip.latitude.values).squeeze()
        lon = np.atleast_1d(precip.longitude.values).squeeze()
        if lat.ndim > 1:
            lat = lat[:, 0]
        if lon.ndim > 1:
            lon = lon[0, :]
        if np.nanmin(lon) >= 0 and np.nanmax(lon) > 180:
            lon = np.where(lon > 180, lon - 360, lon)
        return daily_24h, accum_120h, times, lat, lon
    except Exception:
        return None, None, None, None, None


def table_exceedance_by_railway_km(main_line, cum_km, lat_arr, lon_arr, daily_24h_3d, accum_120h_3d, times):
    """DataFrame com colunas: Limiar, Quilometragem (km), Valor (mm), Dia."""
    if main_line is None or cum_km is None or len(main_line) < 2:
        return pd.DataFrame(columns=["Limiar", "Quilometragem (km)", "Valor (mm)", "Dia"])
    z_24h = np.nanmax(daily_24h_3d, axis=0)
    z_120h = np.nanmax(accum_120h_3d, axis=0)
    idx_24 = np.nanargmax(daily_24h_3d, axis=0)
    idx_120 = np.nanargmax(accum_120h_3d, axis=0)
    nlat, nlon = z_24h.shape[0], z_24h.shape[1]
    rows = []
    for i in range(nlat):
        for j in range(nlon):
            v = float(z_24h[i, j])
            if v > THRESHOLD_24H_MM:
                lat_c = float(lat_arr[i]) if lat_arr.ndim == 1 else float(lat_arr[i, j])
                lon_c = float(lon_arr[j]) if lon_arr.ndim == 1 else float(lon_arr[i, j])
                km = _closest_km_on_line(main_line, cum_km, lat_c, lon_c)
                if km is not None:
                    dia = pd.Timestamp(times[int(idx_24[i, j])]).strftime("%Y-%m-%d")
                    rows.append({"Limiar": "24h > 49 mm", "Quilometragem (km)": round(km, 1), "Valor (mm)": round(v, 1), "Dia": dia})
    for i in range(nlat):
        for j in range(nlon):
            v = float(z_120h[i, j])
            if v > THRESHOLD_120H_MM:
                lat_c = float(lat_arr[i]) if lat_arr.ndim == 1 else float(lat_arr[i, j])
                lon_c = float(lon_arr[j]) if lon_arr.ndim == 1 else float(lon_arr[i, j])
                km = _closest_km_on_line(main_line, cum_km, lat_c, lon_c)
                if km is not None:
                    dia = pd.Timestamp(times[int(idx_120[i, j])]).strftime("%Y-%m-%d")
                    rows.append({"Limiar": "120h > 250 mm", "Quilometragem (km)": round(km, 1), "Valor (mm)": round(v, 1), "Dia": dia})
    if not rows:
        return pd.DataFrame(columns=["Limiar", "Quilometragem (km)", "Valor (mm)", "Dia"])
    df = pd.DataFrame(rows)
    df = df.sort_values("Valor (mm)", ascending=False).drop_duplicates(
        subset=["Limiar", "Quilometragem (km)"], keep="first"
    )
    df = df.sort_values(["Limiar", "Quilometragem (km)"]).reset_index(drop=True)
    return df


def group_exceed_table_rows(rows: list[dict]) -> list[dict]:
    """
    Agrupa linhas consecutivas com mesmo limiar e mm em faixas de km (ex.: 13–22).
    Aceita chaves Km/Quilometragem (km) e Valor/Valor (mm).
    """
    if not rows:
        return []

    def _km(r: dict) -> float:
        raw = r.get("Km", r.get("Quilometragem (km)", r.get("Quilometragem")))
        return float(raw)

    def _val(r: dict) -> float | None:
        raw = r.get("Valor", r.get("Valor (mm)"))
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            return None
        return round(float(raw), 1)

    def _km_label(a: float, b: float) -> str:
        def _one(x: float) -> str:
            return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}"

        if abs(a - b) < 0.05:
            return _one(a)
        return f"{_one(a)}–{_one(b)}"

    ordered = sorted(rows, key=lambda r: (str(r.get("Limiar", "")), _km(r)))
    out: list[dict] = []
    i = 0
    while i < len(ordered):
        row = ordered[i]
        lim = row.get("Limiar")
        val = _val(row)
        km_lo = _km(row)
        km_hi = km_lo
        dia = row.get("Dia")
        j = i + 1
        while j < len(ordered):
            nxt = ordered[j]
            if str(nxt.get("Limiar", "")) != str(lim) or _val(nxt) != val:
                break
            km_n = _km(nxt)
            if abs(km_n - km_hi - 1.0) > 0.05:
                break
            km_hi = km_n
            j += 1
        out.append(
            {
                "Limiar": lim,
                "Km": _km_label(km_lo, km_hi),
                "Valor": val,
                "Dia": dia,
            }
        )
        i = j
    return out


def compute_ruler_segments(df_exceed, km_max, segment_km=10):
    """
    A partir da tabela de ultrapassagens, retorna segmentos para a régua.
    severity: 0=normal, 1=24h, 2=5d+, 3=24h e 5d+ no mesmo segmento.
    Retorna lista de dicts: {km_start, km_end, severity}.
    """
    seg_24 = set()
    seg_long = set()
    if not df_exceed.empty and "Quilometragem (km)" in df_exceed.columns and "Limiar" in df_exceed.columns:
        for _, row in df_exceed.iterrows():
            km = float(row["Quilometragem (km)"])
            seg_start = int(km // segment_km) * segment_km
            kind = limiar_kind_from_label(str(row.get("Limiar", "")))
            if kind == "24h":
                seg_24.add(seg_start)
            elif kind in ("5d", "7d", "30d"):
                seg_long.add(seg_start)
    segments = []
    k = 0
    while k < km_max:
        km_end = min(k + segment_km, km_max)
        severity = 3 if (k in seg_24 and k in seg_long) else (1 if k in seg_24 else (2 if k in seg_long else 0))
        segments.append({"km_start": k, "km_end": km_end, "severity": severity})
        k += segment_km
    return segments


def get_last_merge_date(data_dir: Path = None):
    """Retorna a data do último arquivo MERGE (YYYY-MM-DD) ou None."""
    d = data_dir or DATA_DIR
    import re
    files = sorted(d.glob(MERGE_RAILWAY_JSON_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    p = files[0]
    m = re.search(r"merge_efc_railway_(\d{8})\.json", p.name)
    if m:
        s = m.group(1)
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    # Nome sem data (ex.: merge_efc_railway.json): tentar extrair do conteúdo JSON
    try:
        import json
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        times = data.get("times") or []
        dates = []
        for t in times:
            if isinstance(t, str) and re.match(r"\d{4}-\d{2}-\d{2}", t):
                dates.append(t[:10])
        if dates:
            return max(dates)
    except Exception:
        pass
    try:
        from datetime import datetime
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return None


def get_merge_info(data_dir: Path = None):
    """Retorna (last_date_str, n_times, path) do último JSON MERGE; (None, 0, None) se não houver."""
    d = data_dir or DATA_DIR
    files = sorted(d.glob(MERGE_RAILWAY_JSON_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None, 0, None
    p = files[0]
    last = get_last_merge_date(data_dir=d)
    n_times = 0
    try:
        import json
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        n_times = len(data.get("times") or [])
    except Exception:
        pass
    return last, n_times, p


def get_last_bndmet_date(csv_path: Path = None):
    """Retorna a data do último dado BNDMET (YYYY-MM-DD) ou None."""
    path = csv_path or BNDMET_DADOS_CSV
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, encoding="utf-8")
        if "dataHora" not in df.columns:
            return None
        df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
        df = df.dropna(subset=["_dt"])
        if df.empty:
            return None
        return df["_dt"].max().strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def get_last_ana_date(csv_path: Path = None):
    """Retorna a data do último dado ANA (YYYY-MM-DD) ou None."""
    path = csv_path or ANA_ADOTADA_CSV
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, encoding="utf-8")
        if "dataHora" not in df.columns:
            return None
        df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
        df = df.dropna(subset=["_dt"])
        if df.empty:
            return None
        return df["_dt"].max().strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def get_last_era5_date(nc_path: Path = None):
    """Retorna a última data disponível no NetCDF ERA5 (YYYY-MM-DD) ou None. Leve (só lê coordenada time)."""
    path = nc_path or NC_PATH
    if not path.exists():
        return None
    try:
        ds = xr.open_dataset(path)
        t = getattr(ds, "time", None)
        if t is None and "precip" in ds:
            t = ds["precip"].coords.get("time")
        if t is not None and len(t) > 0:
            out = pd.Timestamp(t.values[-1]).strftime("%Y-%m-%d")
        else:
            out = None
        ds.close()
        return out
    except Exception:
        return None


def get_last_graphcast_date(nc_path: Path = None):
    """Retorna a última data disponível no NetCDF GraphCast (YYYY-MM-DD) ou None. Leve (só lê coordenada time)."""
    path = nc_path or GRAPHCAST_NC_PATH
    if not path.exists():
        return None
    try:
        ds = xr.open_dataset(path)
        t = getattr(ds, "time", None)
        if t is None and "precip" in ds:
            t = ds["precip"].coords.get("time")
        if t is not None and len(t) > 0:
            out = pd.Timestamp(t.values[-1]).strftime("%Y-%m-%d")
        else:
            out = None
        ds.close()
        return out
    except Exception:
        return None


def _monitor_use_bq() -> bool:
    import os

    return (os.environ.get("USE_BQ") or "").strip().lower() in ("1", "true", "yes")


def _load_station_catalog_from_bq(
    dataset: str | None,
    *,
    series_source: str,
) -> pd.DataFrame | None:
    """Catálogo de estações a partir de ``locations`` + ``clima_series`` (Cloud Run sem CSV)."""
    import os

    ds = (dataset or os.environ.get("BQ_DATASET") or "efc_clima").strip() or "efc_clima"
    if not ds and not _monitor_use_bq():
        return None
    try:
        from google.cloud import bigquery

        from gcp.bq_config import get_bq_fully_qualified_prefix
        from gcp.bq_load_clima import _bq_client

        client = _bq_client()
        if client is None:
            return None
        pfx = get_bq_fully_qualified_prefix(ds)
        sql = f"""
            SELECT DISTINCT
              REGEXP_REPLACE(l.location_id, r'^estacao_', '') AS codEstacao,
              l.lat AS latitude,
              l.lon AS longitude,
              COALESCE(
                NULLIF(TRIM(CAST(l.extra AS STRING)), ''),
                REGEXP_REPLACE(l.location_id, r'^estacao_', '')
              ) AS nome
            FROM `{pfx}.locations` l
            WHERE l.source = 'estacao'
              AND l.lat IS NOT NULL
              AND l.lon IS NOT NULL
              -- Prefer stations that ever had precip for this series_source, but do NOT
              -- require recent rows: markers should still render (stale/gray) when coords exist.
              AND (
                EXISTS (
                  SELECT 1
                  FROM `{pfx}.clima_series` cs
                  WHERE cs.location_id = l.location_id
                    AND cs.source = @series_source
                    AND cs.variable = 'precip_mm'
                  LIMIT 1
                )
                OR NOT EXISTS (
                  SELECT 1
                  FROM `{pfx}.clima_series` cs2
                  WHERE cs2.location_id = l.location_id
                    AND cs2.variable = 'precip_mm'
                  LIMIT 1
                )
              )
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("series_source", "STRING", series_source),
            ]
        )
        df = client.query(sql, job_config=cfg).to_dataframe()
        if df is None or df.empty:
            return None
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        df["codEstacao"] = df["codEstacao"].astype(str).str.strip()
        df = df.dropna(subset=["latitude", "longitude"])
        df = df[df["codEstacao"] != ""]
        if df.empty:
            return None
        # Overlay display names from local CSV catalogs when present (BQ extra is INT).
        try:
            name_map: dict[str, str] = {}
            for csv_path in (BNDMET_ESTACOES_CSV, ANA_ESTACOES_CSV):
                if not csv_path.exists():
                    continue
                cdf = pd.read_csv(csv_path, encoding="utf-8")
                cod_c = next((c for c in ["codEstacao", "codigo", "id"] if c in cdf.columns), None)
                nome_c = next((c for c in cdf.columns if c.lower() in ("nome", "name", "dc_nome")), None)
                if not cod_c or not nome_c:
                    continue
                for _, row in cdf.iterrows():
                    cod = str(row[cod_c]).strip()
                    nome = str(row[nome_c]).strip()
                    if cod and nome and nome.lower() not in ("nan", "none"):
                        name_map[cod] = nome
            if name_map:
                df["nome"] = df.apply(
                    lambda r: name_map.get(str(r["codEstacao"]), r["nome"]),
                    axis=1,
                )
        except Exception:
            pass
        return df.reset_index(drop=True)
    except Exception:
        return None


def _station_csv_usable(df: pd.DataFrame) -> bool:
    """CSV válido só se tiver coordenadas e pelo menos uma estação com código."""
    if df is None or df.empty:
        return False
    lat_c = next((c for c in df.columns if "lat" in c.lower()), None)
    lon_c = next((c for c in df.columns if "lon" in c.lower()), None)
    cod_c = next((c for c in ["codEstacao", "codigo", "id"] if c in df.columns), None)
    if not (lat_c and lon_c):
        return False
    sub = df.dropna(subset=[lat_c, lon_c])
    if sub.empty:
        return False
    if cod_c:
        sub = sub[sub[cod_c].astype(str).str.strip() != ""]
    return not sub.empty


def load_bndmet_estacoes(csv_path: Path = None, *, bq_dataset: str | None = None):
    """Retorna DataFrame de estações BNDMET ou None (BigQuery se USE_BQ, senão CSV)."""
    import os

    ds = bq_dataset or os.environ.get("BQ_DATASET")
    if ds or _monitor_use_bq():
        bq_df = _load_station_catalog_from_bq(ds, series_source="estacoes")
        if bq_df is not None and not bq_df.empty:
            return bq_df
    path = csv_path or BNDMET_ESTACOES_CSV
    if path.exists():
        try:
            df = pd.read_csv(path, encoding="utf-8")
            if _station_csv_usable(df):
                return df
        except Exception:
            pass
    return None


def load_ana_estacoes(csv_path: Path = None, *, bq_dataset: str | None = None):
    """Retorna DataFrame de estações ANA ou None (BigQuery se USE_BQ, senão CSV)."""
    import os

    ds = bq_dataset or os.environ.get("BQ_DATASET")
    if ds or _monitor_use_bq():
        bq_df = _load_station_catalog_from_bq(ds, series_source="ana")
        if bq_df is not None and not bq_df.empty:
            return bq_df
    path = csv_path or ANA_ESTACOES_CSV
    if path.exists():
        try:
            df = pd.read_csv(path, encoding="utf-8")
            if _station_csv_usable(df):
                lat_c = next((c for c in df.columns if "lat" in c.lower()), None)
                lon_c = next((c for c in df.columns if "lon" in c.lower()), None)
                cod_c = next((c for c in ["codEstacao", "codigo", "id"] if c in df.columns), None)
                df[lat_c] = pd.to_numeric(df[lat_c], errors="coerce")
                df[lon_c] = pd.to_numeric(df[lon_c], errors="coerce")
                df = df.dropna(subset=[lat_c, lon_c])
                if cod_c:
                    df[cod_c] = df[cod_c].astype(str).str.strip()
                    df = df[df[cod_c] != ""]
                return df.reset_index(drop=True)
        except Exception:
            pass
    return None


def load_orion_estacoes(*, bq_dataset: str | None = None):
    """Catálogo Orion/EWS (pluviômetros) a partir de ``locations`` + ``orion_devices``."""
    import os

    ds = (bq_dataset or os.environ.get("BQ_DATASET") or "efc_clima").strip() or "efc_clima"
    if not ds and not _monitor_use_bq():
        return None
    try:
        from gcp.bq_config import get_bq_fully_qualified_prefix
        from gcp.bq_load_clima import _bq_client

        client = _bq_client()
        if client is None:
            return None
        pfx = get_bq_fully_qualified_prefix(ds)
        sql = f"""
            SELECT
              REGEXP_REPLACE(l.location_id, r'^ews_', '') AS codEstacao,
              l.lat AS latitude,
              l.lon AS longitude,
              COALESCE(
                NULLIF(TRIM(d.name), ''),
                REGEXP_REPLACE(l.location_id, r'^ews_', '')
              ) AS nome
            FROM `{pfx}.locations` l
            LEFT JOIN `{pfx}.orion_devices` d
              ON d.location_id = l.location_id
            WHERE l.source = 'orion'
              AND l.lat IS NOT NULL
              AND l.lon IS NOT NULL
              AND NOT STARTS_WITH(l.location_id, 'ews_IMEI_')
              AND EXISTS (
                SELECT 1 FROM `{pfx}.clima_series` cs
                WHERE cs.location_id = l.location_id
                  AND cs.source = 'orion'
                  AND cs.variable = 'precip_mm'
                LIMIT 1
              )
        """
        df = client.query(sql).to_dataframe()
        if df is None or df.empty:
            return None
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        df["codEstacao"] = df["codEstacao"].astype(str).str.strip()
        # Keep human name; UI labels network as EWS separately.
        df["nome"] = df["nome"].astype(str).str.strip()
        df = df.dropna(subset=["latitude", "longitude"])
        df = df[df["codEstacao"] != ""]
        df = df[~df["codEstacao"].str.startswith("IMEI_")]
        return df.reset_index(drop=True) if not df.empty else None
    except Exception:
        return None


def get_orion_accum_hours_stale(
    ref_date: str,
    *,
    hours: int = 120,
    stale_days: int = 3,
    bq_dataset: str | None = None,
) -> tuple[dict[str, float], set[str], dict[str, str]]:
    """Acumulado Orion/EWS (clima_series source=orion) — códigos sem prefixo ews_."""
    ds = bq_dataset or os.environ.get("BQ_DATASET") or "efc_clima"
    return _station_accum_stale_from_clima_series(
        ref_date,
        hours=hours,
        stale_days=stale_days,
        sources=("orion",),
        dataset=ds,
    )


# Máximo de linhas no pivot BNDMET para evitar MessageSizeError no Streamlit (>200 MB)
BNDMET_PIVOT_MAX_ROWS = 5000


def load_bndmet_dados(csv_path: Path = None):
    """Retorna (df_long, df_pivot) para gráficos BNDMET ou (None, None). Limita pivot a BNDMET_PIVOT_MAX_ROWS linhas (últimos dias) para evitar payload excessivo no app."""
    path = csv_path or BNDMET_DADOS_CSV
    if not path.exists():
        return None, None
    try:
        df = pd.read_csv(path, encoding="utf-8")
        df = _normalize_bndmet_dados_columns(df)
        if df.empty or "codFenomeno" not in df.columns:
            return None, None
        if "dataHora" in df.columns and "valor" in df.columns:
            df = df.copy()
            df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
            df = df.dropna(subset=["_dt"])
            df["_valor"] = pd.to_numeric(df["valor"], errors="coerce")
            df = df.dropna(subset=["_valor"])
            df["_var"] = df["codFenomeno"].astype(str).map(lambda c: FENOMENO_LABELS.get(c, c))
            agg = df.groupby([df["_dt"].dt.floor("h"), "_var"])["_valor"].mean().reset_index()
            pivot = agg.pivot(index="_dt", columns="_var", values="_valor").sort_index()
            pivot.index = pd.to_datetime(pivot.index)
            if len(pivot) > BNDMET_PIVOT_MAX_ROWS:
                pivot = pivot.tail(BNDMET_PIVOT_MAX_ROWS)
            return df, pivot
        if "data" not in df.columns:
            return None, None
        import ast
        rows = []
        for _, row in df.iterrows():
            raw = row.get("data")
            if pd.isna(raw) or raw == "" or raw == "[]":
                continue
            if isinstance(raw, str) and raw.strip().startswith("["):
                try:
                    raw = ast.literal_eval(raw)
                except Exception:
                    continue
            if not isinstance(raw, list) or len(raw) == 0:
                continue
            cod_fen = str(row.get("codFenomeno", ""))
            var = FENOMENO_LABELS.get(cod_fen, cod_fen)
            for pt in raw:
                if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                    continue
                ts_ms, val = pt[0], pt[1]
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    continue
                try:
                    dt = pd.to_datetime(int(ts_ms), unit="ms", utc=True).tz_localize(None)
                    rows.append({"_dt": dt, "_valor": float(val), "_var": var})
                except (ValueError, TypeError):
                    continue
        if not rows:
            return None, None
        df_long = pd.DataFrame(rows)
        agg = df_long.groupby([df_long["_dt"].dt.floor("h"), "_var"])["_valor"].mean().reset_index()
        pivot = agg.pivot(index="_dt", columns="_var", values="_valor").sort_index()
        pivot.index = pd.to_datetime(pivot.index)
        if len(pivot) > BNDMET_PIVOT_MAX_ROWS:
            pivot = pivot.tail(BNDMET_PIVOT_MAX_ROWS)
        return df_long, pivot
    except Exception:
        return None, None


def load_ana_dados(csv_path: Path = None):
    """Retorna (df_long, df_pivot) para séries ANA adotadas/detalhadas normalizadas."""
    path = csv_path or ANA_ADOTADA_CSV
    if not path.exists():
        return None, None
    try:
        df = pd.read_csv(path, encoding="utf-8")
        need = {"dataHora", "codEstacao", "variavel", "valor"}
        if df.empty or not need.issubset(df.columns):
            return None, None
        df = df.copy()
        df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
        df = df.dropna(subset=["_dt"])
        df["_valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df = df.dropna(subset=["_valor"])
        if df.empty:
            return None, None
        df["_var"] = df["variavel"].astype(str).map(lambda c: ANA_LABELS.get(str(c), str(c)))
        agg = df.groupby([df["_dt"].dt.floor("h"), "_var"])["_valor"].mean().reset_index()
        pivot = agg.pivot(index="_dt", columns="_var", values="_valor").sort_index()
        pivot.index = pd.to_datetime(pivot.index)
        if len(pivot) > BNDMET_PIVOT_MAX_ROWS:
            pivot = pivot.tail(BNDMET_PIVOT_MAX_ROWS)
        return df, pivot
    except Exception:
        return None, None


def get_ana_daily_precip_by_station(csv_path: Path = None, last_n_days: int = 7):
    """Série diária de precipitação ANA por estação: {cod: [(date, mm), ...]}."""
    path = csv_path or ANA_ADOTADA_CSV
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, encoding="utf-8")
        need = {"dataHora", "codEstacao", "variavel", "valor"}
        if df.empty or not need.issubset(df.columns):
            return {}
        df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df = df.dropna(subset=["_dt", "valor"])
        df = df[df["variavel"].astype(str).str.startswith("precip_mm")]
        df = df[~df["variavel"].astype(str).str.contains("bruto", case=False, na=False)]
        if df.empty:
            return {}
        lo = pd.Timestamp.now().normalize() - pd.Timedelta(days=max(1, int(last_n_days)) - 1)
        df = df[df["_dt"].dt.normalize() >= lo]
        df["_day"] = df["_dt"].dt.normalize()
        daily = (
            df.groupby(["codEstacao", "_day"], as_index=False)["valor"].sum().sort_values(["codEstacao", "_day"])
        )
        out: dict[str, list[tuple[str, float]]] = {}
        for cod, sub in daily.groupby("codEstacao"):
            out[str(cod)] = [
                (pd.Timestamp(day).strftime("%Y-%m-%d"), float(val))
                for day, val in zip(sub["_day"], sub["valor"])
            ]
        return out
    except Exception:
        return {}



def _accum_hours_to_days(hours: int) -> int:
    """Converte janela em horas do menu Acumulado para dias civis em clima_series."""
    h = int(hours or 24)
    if h <= 24:
        return 1
    if h <= 120:
        return 5
    if h <= 168:
        return 7
    if h <= 720:
        return 30
    return max(1, (h + 23) // 24)


def _station_accum_stale_from_clima_series(
    ref_date: str,
    *,
    hours: int = 120,
    stale_days: int = 3,
    sources: tuple[str, ...] | list[str] = ("estacoes", "bndmet"),
    dataset: str | None = None,
) -> tuple[dict[str, float], set[str], dict[str, str]]:
    """
    Fallback do mapa quando ``bndmet_horario`` / CSV horário não existem no Cloud Run.

    Lê ``clima_series`` (precip_mm diário/sub-diário já ingerido) e devolve
    o mesmo contrato ``(accum, stale, last_obs)`` das funções horárias.
    """
    accum: dict[str, float] = {}
    last_obs: dict[str, str] = {}
    stale: set[str] = set()
    srcs = [str(s).strip().lower() for s in (sources or ()) if str(s).strip()]
    if not srcs:
        return accum, stale, last_obs

    ref_day = pd.Timestamp(str(ref_date)[:10]).normalize().tz_localize("UTC")
    ref_end = ref_day + pd.Timedelta(days=1)
    now = pd.Timestamp.now(tz="UTC")
    end_ts = min(ref_end, now)
    stale_cutoff = end_ts - pd.Timedelta(days=int(stale_days))
    days = _accum_hours_to_days(hours)
    d_end = str(ref_date)[:10]
    d_start = (pd.Timestamp(d_end) - pd.Timedelta(days=days - 1)).strftime("%Y-%m-%d")
    # Recência: olha um pouco além da janela de acumulado (estações sem chuva na janela).
    lookback = max(int(stale_days) + 2, days, 14)
    d_look = (pd.Timestamp(d_end) - pd.Timedelta(days=lookback - 1)).strftime("%Y-%m-%d")

    try:
        from google.cloud import bigquery

        from gcp.bq_config import get_bq_fully_qualified_prefix
        from gcp.bq_load_clima import _bq_client

        client = _bq_client()
        if client is None:
            return accum, stale, last_obs
        ds = (dataset or os.environ.get("BQ_DATASET") or "efc_clima").strip() or "efc_clima"
        pfx = get_bq_fully_qualified_prefix(ds)
        full = f"{pfx}.clima_series"
        window_agg = "MAX(daily_mm)" if days <= 1 else "SUM(daily_mm)"
        sql = f"""
        WITH per_slot AS (
          SELECT
            s.location_id,
            DATE(s.date) AS d,
            COALESCE(s.valid_time, TIMESTAMP(DATE(s.date))) AS slot_ts,
            MAX(s.value) AS slot_mm
          FROM `{full}` s
          WHERE s.variable = 'precip_mm'
            AND LOWER(s.source) IN UNNEST(@sources)
            AND DATE(s.date) BETWEEN @d_look AND @d1
            AND DATE(s.date) <= CURRENT_DATE()
          GROUP BY s.location_id, d, slot_ts
        ),
        daily AS (
          SELECT location_id, d, SUM(slot_mm) AS daily_mm, MAX(slot_ts) AS day_last
          FROM per_slot
          GROUP BY location_id, d
        ),
        last_any AS (
          SELECT location_id, MAX(day_last) AS last_ts
          FROM daily
          WHERE d <= @d1
          GROUP BY location_id
        ),
        windowed AS (
          SELECT location_id, {window_agg} AS total_mm
          FROM daily
          WHERE d BETWEEN @d0 AND @d1
          GROUP BY location_id
        )
        SELECT
          COALESCE(w.location_id, l.location_id) AS location_id,
          COALESCE(w.total_mm, 0.0) AS total_mm,
          l.last_ts AS last_ts
        FROM windowed w
        FULL OUTER JOIN last_any l USING (location_id)
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("sources", "STRING", srcs),
                bigquery.ScalarQueryParameter("d0", "DATE", d_start),
                bigquery.ScalarQueryParameter("d1", "DATE", d_end),
                bigquery.ScalarQueryParameter("d_look", "DATE", d_look),
            ]
        )
        df = client.query(sql, job_config=cfg).to_dataframe()
    except Exception:
        return {}, set(), {}

    if df is None or df.empty:
        return accum, stale, last_obs

    for _, row in df.iterrows():
        lid = str(row.get("location_id") or "")
        cod = (
            lid.replace("estacao_", "")
            .replace("bndmet_", "")
            .replace("ana_", "")
            .replace("ews_", "")
            .strip()
        )
        if not cod or "IMEI_" in cod.upper():
            continue
        try:
            mm = float(row.get("total_mm") or 0.0)
        except (TypeError, ValueError):
            mm = 0.0
        # Soft daily totals mis-tagged as hourly tips sum to multi-thousand mm.
        if mm > float(hours) * 120.0:
            continue
        accum[cod] = round(mm, 2)
        tsv = _bndmet_horario_ts_utc(row.get("last_ts"))
        if tsv is None:
            continue
        last_obs[cod] = tsv.strftime("%Y-%m-%d %H:%M UTC")
        if tsv < stale_cutoff:
            stale.add(cod)

    stale |= _bndmet_stale_codes_from_last_obs(last_obs, stale_cutoff)
    return accum, stale, last_obs


def get_ana_accum_hours_stale(
    ref_date: str,
    *,
    hours: int = 120,
    stale_days: int = 3,
    csv_path: Path | None = None,
    bq_dataset: str | None = None,
) -> tuple[dict[str, float], set[str], dict[str, str]]:
    """
    Acumulado de precipitação ANA nas últimas ``hours`` horas e marca estações sem dado recente.

    Ordem: série adotada local (`ANA_ADOTADA_CSV`) + merge/fallback ``clima_series`` (source=ana).
    CSV antigo sozinho não deve esconder o BQ (Cloud Run / mapa).
    """
    accum: dict[str, float] = {}
    last_obs: dict[str, str] = {}
    stale: set[str] = set()

    ref_day = pd.Timestamp(ref_date).normalize().tz_localize("UTC")
    ref_end = ref_day + pd.Timedelta(days=1)
    now = pd.Timestamp.now(tz="UTC")
    end_ts = min(ref_end, now)
    start_ts = end_ts - pd.Timedelta(hours=int(hours))
    # Recência relativa ao fim do dia selecionado (não ao "agora"), senão
    # datas históricas pintam todas as estações de cinza e o menu Acumulado parece inerte.
    stale_cutoff = end_ts - pd.Timedelta(days=int(stale_days))

    path = csv_path or ANA_ADOTADA_CSV
    if path.exists():
        try:
            df = pd.read_csv(path, encoding="utf-8")
            need = {"dataHora", "codEstacao", "variavel", "valor"}
            if not df.empty and need.issubset(df.columns):
                df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
                df = df.dropna(subset=["_dt"])
                df["_dt"] = df["_dt"].apply(
                    lambda x: x.tz_localize("UTC") if x.tzinfo is None else x.tz_convert("UTC")
                )
                df["codEstacao"] = df["codEstacao"].astype(str)
                df["_valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0)

                # Recência: qualquer variável ANA até o fim do dia de referência.
                df_asof = df[df["_dt"] < end_ts]
                last_series = (
                    df_asof.groupby("codEstacao")["_dt"].max()
                    if not df_asof.empty
                    else pd.Series(dtype="datetime64[ns, UTC]")
                )
                for cod, ts in last_series.items():
                    cod = str(cod)
                    if ts is None or pd.isna(ts):
                        continue
                    tsv = pd.Timestamp(ts)
                    if tsv.tzinfo is None:
                        tsv = tsv.tz_localize("UTC")
                    else:
                        tsv = tsv.tz_convert("UTC")
                    last_obs[cod] = tsv.strftime("%Y-%m-%d %H:%M UTC")
                    if tsv < stale_cutoff:
                        stale.add(cod)

                # Acumulado: somente chuva.
                df_prec = df[df["variavel"].astype(str) == "precip_mm"].copy()
                if not df_prec.empty:
                    win = df_prec[(df_prec["_dt"] >= start_ts) & (df_prec["_dt"] < end_ts)]
                    if not win.empty:
                        g = win.groupby("codEstacao")["_valor"].sum()
                        accum = {str(k): round(float(v), 2) for k, v in g.items()}
        except Exception:
            accum, last_obs, stale = {}, {}, set()

    csv_result = (accum, stale, last_obs)
    ds = bq_dataset or os.environ.get("BQ_DATASET") or "efc_clima"
    series = _station_accum_stale_from_clima_series(
        ref_date,
        hours=hours,
        stale_days=stale_days,
        sources=("ana",),
        dataset=ds,
    )
    if not csv_result[2]:
        return series if series[2] else ({}, set(), {})
    if not series[2]:
        return csv_result
    return _bndmet_merge_accum_stale_last_obs(csv_result, series, stale_cutoff=stale_cutoff)


def load_graphcast(nc_path: Path = None):
    """Retorna (precip_da, times, lat, lon) ou (None, None, None, None)."""
    path = nc_path or GRAPHCAST_NC_PATH
    if not path.exists():
        return None, None, None, None
    try:
        ds = xr.open_dataset(path)
        precip = ds["precip"]
        if "latitude" not in precip.dims:
            lat_c = next((c for c in precip.coords if "lat" in c.lower()), None)
            lon_c = next((c for c in precip.coords if "lon" in c.lower()), None)
            if lat_c and lon_c:
                precip = precip.rename({lat_c: "latitude", lon_c: "longitude"})
        times = precip.time.values
        lat = np.atleast_1d(precip.latitude.values).squeeze()
        lon = np.atleast_1d(precip.longitude.values).squeeze()
        if lat.ndim > 1:
            lat = lat[:, 0]
        if lon.ndim > 1:
            lon = lon[0, :]
        if np.nanmin(lon) >= 0 and np.nanmax(lon) > 180:
            lon = np.where(lon > 180, lon - 360, lon)
        return precip, times, lat, lon
    except Exception:
        return None, None, None, None


def get_detail_for_km(km_float, df_exceed, segment_km=20):
    """Retorna dict com status 24h/120h e linhas da tabela para o km (trecho). Texto em preto."""
    out = {"km": km_float, "segment": int(float(km_float) // segment_km) * segment_km, "rows_24": [], "rows_72": [], "status_24": "Normal", "status_72": "Normal", "max_24": None, "max_72": None}
    if df_exceed is None or df_exceed.empty:
        return out
    km = float(km_float)
    seg_start = int(km // segment_km) * segment_km
    for _, row in df_exceed.iterrows():
        r_km = float(row.get("Quilometragem (km)", 0))
        r_seg = int(r_km // segment_km) * segment_km
        if r_seg != seg_start:
            continue
        limiar = str(row.get("Limiar", ""))
        if "24h" in limiar:
            out["rows_24"].append(row)
            v = row.get("Valor (mm)", 0)
            if out["max_24"] is None or v > out["max_24"]:
                out["max_24"] = v
        if "120h" in limiar:
            out["rows_72"].append(row)
            v = row.get("Valor (mm)", 0)
            if out["max_72"] is None or v > out["max_72"]:
                out["max_72"] = v
    if out["rows_24"]:
        out["status_24"] = "Alerta"
    if out["rows_72"]:
        out["status_72"] = "Alerta"
    return out


def get_merge_series_at_km(km_float: float, last_n_days: int = 7, data_dir: Path = None):
    """
    Série horária MERGE no km (últimos N dias).
    Retorna (list of datetime, list of float mm) ou ([], []) se sem dados.
    """
    import json
    import re
    d = data_dir or DATA_DIR
    files = sorted(d.glob(MERGE_RAILWAY_JSON_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return [], []
    try:
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], []
    km_list = data.get("km", [])
    times_raw = data.get("times", [])
    precip_mm = data.get("precip_mm", [])
    if not km_list or not times_raw or not precip_mm:
        return [], []
    km_arr = np.array(km_list, dtype=float)
    idx_km = int(np.argmin(np.abs(km_arr - float(km_float))))
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=last_n_days)
    out_ts, out_val = [], []
    for ti, t_str in enumerate(times_raw):
        try:
            ts = pd.Timestamp(t_str)
            if ts < cutoff:
                continue
            v = precip_mm[ti][idx_km] if ti < len(precip_mm) and idx_km < len(precip_mm[ti]) else 0.0
            out_ts.append(ts)
            out_val.append(float(v))
        except Exception:
            continue
    return out_ts, out_val


def get_merge_along_railway_stats(th_24: float = 5.0, th_72: float = 5.0, data_dir: Path = None, ref_date: str = None):
    """
    Estatísticas MERGE ao longo de toda a ferrovia.
    ref_date: se fornecido (YYYY-MM-DD), usa o final desse dia como referência; senão usa o último timestamp do arquivo.
    Retorna dict com km, sum24, sum72, masks e máximos, ou None.
    """
    import json

    d = data_dir or DATA_DIR
    files = sorted(d.glob(MERGE_RAILWAY_JSON_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    km_list = data.get("km", [])
    times_raw = data.get("times", [])
    precip_mm = data.get("precip_mm", [])
    if not km_list or not times_raw or not precip_mm:
        return None

    km_arr = np.array(km_list, dtype=float)
    n_km = len(km_arr)
    n_t = min(len(times_raw), len(precip_mm))
    if n_t == 0 or n_km == 0:
        return None

    if ref_date is not None:
        try:
            t_ref = pd.Timestamp(ref_date) + pd.Timedelta(hours=23, minutes=59)
        except Exception:
            t_ref = max(pd.Timestamp(t) for t in times_raw if t is not None)
    else:
        try:
            t_ref = max(pd.Timestamp(t) for t in times_raw if t is not None)
        except Exception:
            return None
    cutoff_24 = t_ref - pd.Timedelta(hours=24)
    cutoff_72 = t_ref - pd.Timedelta(hours=72)

    sum24 = np.zeros(n_km, dtype=float)
    sum72 = np.zeros(n_km, dtype=float)

    for ti in range(n_t):
        try:
            ts = pd.Timestamp(times_raw[ti])
        except Exception:
            continue
        if ts > t_ref:
            continue
        row = precip_mm[ti] if ti < len(precip_mm) else None
        if row is None:
            continue
        row_arr = np.array(row, dtype=float)
        if row_arr.shape[0] < n_km:
            tmp = np.zeros(n_km, dtype=float)
            tmp[: row_arr.shape[0]] = row_arr
            row_arr = tmp
        if ts >= cutoff_24:
            sum24 += np.nan_to_num(row_arr, nan=0.0)
        if ts >= cutoff_72:
            sum72 += np.nan_to_num(row_arr, nan=0.0)

    mask24 = sum24 > float(th_24)
    mask72 = sum72 > float(th_72)

    max24_val = float(sum24.max()) if np.any(sum24 > 0) else None
    max72_val = float(sum72.max()) if np.any(sum72 > 0) else None
    max24_km = float(km_arr[int(sum24.argmax())]) if max24_val is not None else None
    max72_km = float(km_arr[int(sum72.argmax())]) if max72_val is not None else None

    return {
        "km": km_arr,
        "sum24": sum24,
        "sum72": sum72,
        "mask24": mask24,
        "mask72": mask72,
        "max24_km": max24_km,
        "max24_val": max24_val,
        "max72_km": max72_km,
        "max72_val": max72_val,
    }


def get_graphcast_precip_by_km(main_line, cumulative_km, nc_path: Path = None, km_step: int = 10):
    """
    Amostra GraphCast precipitação ao longo da ferrovia, a cada km_step km.
    Inclui 24h (por dia) e acumulado móvel 120h (20 passos de 6h).
    Retorna dict: {"24h": { day_label: { km: precip_mm } }, "120h": { day_label: { km: accum_mm } } } ou None.
    """
    precip_da, times, lat, lon = load_graphcast(nc_path)
    if precip_da is None or times is None or len(times) == 0:
        return None
    if main_line is None or cumulative_km is None or len(cumulative_km) < 2:
        return None

    n_time = len(times)
    # Acumulado móvel 120h = 20 passos de 6h
    steps_120h = 20
    accum_120h_da = precip_da.rolling(time=steps_120h, min_periods=1).sum()

    km_max = int(np.ceil(float(cumulative_km[-1])))
    km_list = list(range(0, km_max + 1, km_step))
    result_24h = {}
    result_120h = {}
    for ti in range(n_time):
        day_label = pd.Timestamp(times[ti]).strftime("%Y-%m-%d")
        vals_24 = precip_da.isel(time=ti).values
        vals_120 = accum_120h_da.isel(time=ti).values
        km_vals_24 = {}
        km_vals_120 = {}
        for km in km_list:
            pos = position_at_km(float(km), main_line, cumulative_km)
            if pos is None:
                continue
            v24 = _sample_grid_nearest(lat, lon, vals_24, pos[0], pos[1], lat_first=True)
            v120 = _sample_grid_nearest(lat, lon, vals_120, pos[0], pos[1], lat_first=True)
            km_vals_24[km] = round(v24, 2)
            km_vals_120[km] = round(v120, 2)
        result_24h[day_label] = km_vals_24
        result_120h[day_label] = km_vals_120
    return {"24h": result_24h, "120h": result_120h}


def _sample_grid_nearest(lat_arr, lon_arr, data_2d, lat_q, lon_q, lat_first=True):
    """Amostra data_2d no ponto (lat_q, lon_q) por vizinho mais próximo."""
    lat_arr = np.asarray(lat_arr).squeeze()
    lon_arr = np.asarray(lon_arr).squeeze()
    i_lat = int(np.argmin(np.abs(lat_arr - lat_q)))
    j_lon = int(np.argmin(np.abs(lon_arr - lon_q)))
    if lat_first:
        return float(np.nan_to_num(data_2d[i_lat, j_lon], nan=0.0))
    return float(np.nan_to_num(data_2d[j_lon, i_lat], nan=0.0))


def get_era5_series_at_km(km_float: float, main_line, cumulative_km, nc_path: Path = None, last_n_days: int = 7):
    """
    Série diária ERA5 (24h) no km para os últimos N dias.
    Retorna (list of datetime, list of float mm) ou ([], []).
    """
    daily_24h, _, times, lat, lon = load_era5(nc_path)
    if daily_24h is None or times is None:
        return [], []
    pos = position_at_km(float(km_float), main_line, cumulative_km)
    if pos is None:
        return [], []
    lat_q, lon_q = pos
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=last_n_days)
    out_ts, out_val = [], []
    for i in range(len(times)):
        try:
            ts = pd.Timestamp(times[i])
            if ts < cutoff:
                continue
            vals = daily_24h.isel(time=i).values
            v = _sample_grid_nearest(lat, lon, vals, lat_q, lon_q, lat_first=True)
            out_ts.append(ts)
            out_val.append(v)
        except Exception:
            continue
    return out_ts, out_val


def get_graphcast_series_at_km(km_float: float, main_line, cumulative_km, nc_path: Path = None):
    """
    Série de previsão GraphCast no km (precip por passo de tempo).
    Retorna (list of datetime, list of float mm) ou ([], []).
    """
    precip_da, times, lat, lon = load_graphcast(nc_path)
    if precip_da is None or times is None:
        return [], []
    pos = position_at_km(float(km_float), main_line, cumulative_km)
    if pos is None:
        return [], []
    lat_q, lon_q = pos
    out_ts, out_val = [], []
    for i in range(len(times)):
        try:
            ts = pd.Timestamp(times[i])
            vals = precip_da.isel(time=i).values
            v = _sample_grid_nearest(lat, lon, vals, lat_q, lon_q, lat_first=True)
            out_ts.append(ts)
            out_val.append(v)
        except Exception:
            continue
    return out_ts, out_val


BNDMET_PRECIP_COD = "I175"


def get_bndmet_precip_for_date(ref_date: str, csv_path: Path = None) -> dict:
    """Retorna {cod_estacao_str: precip_mm_float} para um dia específico (formato YYYY-MM-DD).
    Tenta BQ primeiro (clima_series source='estacoes'), fallback CSV local."""
    target = pd.Timestamp(ref_date).date()
    # 1) Tentar BQ
    try:
        from gcp.bq_config import get_bq_fully_qualified_prefix
        from gcp.bq_load_clima import _bq_client

        client = _bq_client()
        if client is not None:
            _pfx = get_bq_fully_qualified_prefix()
            sql = f"""
                SELECT l.location_id, l.extra, cs.value
                FROM `{_pfx}.clima_series` cs
                JOIN `{_pfx}.locations` l ON cs.location_id = l.location_id
                WHERE cs.source = 'estacoes'
                  AND cs.variable = 'precip_mm'
                  AND cs.date = '{target.isoformat()}'
                  AND l.source = 'estacao'
            """
            df = client.query(sql).to_dataframe()
            if not df.empty:
                out = {}
                for _, row in df.iterrows():
                    extra = row.get("extra")
                    loc_id = str(row.get("location_id", ""))
                    cod = str(extra) if (extra and str(extra) != "None") else loc_id.replace("estacao_", "")
                    out[cod] = float(row["value"])
                return out
    except Exception:
        pass
    # 2) Fallback: CSV local
    path = csv_path or BNDMET_DADOS_CSV
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, encoding="utf-8")
        df = _normalize_bndmet_dados_columns(df)
        df = df[df["codFenomeno"].astype(str) == BNDMET_PRECIP_COD].copy()
        df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
        df = df.dropna(subset=["_dt"])
        df["_date"] = df["_dt"].dt.date
        df = df[df["_date"] == target]
        df["_valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0)
        daily = df.groupby("codEstacao")["_valor"].sum()
        return {str(k): round(float(v), 2) for k, v in daily.items()}
    except Exception:
        return {}


_BNDMET_BQ_TABLE_OK: dict[str, bool] = {}


def _bndmet_horario_bq_available(dataset: str, table: str = "bndmet_horario") -> bool:
    """Evita consultas repetidas a tabela horária inexistente no dataset (atraso no mapa)."""
    if (os.environ.get("MONITOR_BNDMET_USE_BQ") or "1").strip().lower() in ("0", "false", "no"):
        return False
    key = f"{dataset}.{table}"
    if key in _BNDMET_BQ_TABLE_OK:
        return _BNDMET_BQ_TABLE_OK[key]
    try:
        from gcp.bq_config import get_bq_fully_qualified_prefix
        from gcp.bq_load_clima import _bq_client

        client = _bq_client()
        if client is None:
            _BNDMET_BQ_TABLE_OK[key] = False
            return False
        full = f"{get_bq_fully_qualified_prefix(dataset)}.{table}"
        client.get_table(full)
        _BNDMET_BQ_TABLE_OK[key] = True
    except Exception:
        _BNDMET_BQ_TABLE_OK[key] = False
    return _BNDMET_BQ_TABLE_OK[key]


def _bndmet_horario_ts_utc(ts) -> pd.Timestamp | None:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    try:
        tsv = pd.Timestamp(ts)
        if tsv.tzinfo is None:
            tsv = tsv.tz_localize("UTC")
        else:
            tsv = tsv.tz_convert("UTC")
        return tsv
    except Exception:
        return None


def _bndmet_accum_last_obs_from_horario_df(
    df: pd.DataFrame,
    *,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    stale_cutoff: pd.Timestamp,
) -> tuple[dict[str, float], set[str], dict[str, str]]:
    """
    ``last_obs`` / ``stale`` usam qualquer fenômeno (recência operacional).
    ``accum`` soma apenas precipitação I175 na janela ``[start_ts, end_ts)``.
    """
    accum: dict[str, float] = {}
    last_obs: dict[str, str] = {}
    stale: set[str] = set()
    if df is None or df.empty or "codEstacao" not in df.columns or "dataHora" not in df.columns:
        return accum, stale, last_obs
    df = df.copy()
    df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
    df = df.dropna(subset=["_dt"])
    df["_dt"] = df["_dt"].apply(
        lambda x: x.tz_localize("UTC") if x.tzinfo is None else x.tz_convert("UTC")
    )
    df["codEstacao"] = df["codEstacao"].astype(str).str.strip()
    df = df[df["codEstacao"] != ""]
    if df.empty:
        return accum, stale, last_obs

    # Recência operacional até o fim da janela (as-of end_ts), não global.
    df_asof = df[df["_dt"] < end_ts]
    last_series = (
        df_asof.groupby("codEstacao")["_dt"].max()
        if not df_asof.empty
        else pd.Series(dtype="datetime64[ns, UTC]")
    )
    for cod, ts in last_series.items():
        tsv = _bndmet_horario_ts_utc(ts)
        if tsv is None:
            continue
        last_obs[str(cod)] = tsv.strftime("%Y-%m-%d %H:%M UTC")
        if tsv < stale_cutoff:
            stale.add(str(cod))

    if "codFenomeno" in df.columns and "valor" in df.columns:
        df_prec = df[df["codFenomeno"].astype(str) == BNDMET_PRECIP_COD].copy()
        df_prec["_valor"] = pd.to_numeric(df_prec["valor"], errors="coerce").fillna(0.0)
        win = df_prec[(df_prec["_dt"] >= start_ts) & (df_prec["_dt"] < end_ts)]
        if not win.empty:
            g = win.groupby("codEstacao")["_valor"].sum()
            accum = {str(k): round(float(v), 2) for k, v in g.items()}
    return accum, stale, last_obs


def _bndmet_stale_codes_from_last_obs(
    last_obs: dict[str, str],
    stale_cutoff: pd.Timestamp,
) -> set[str]:
    stale: set[str] = set()
    for cod, txt in last_obs.items():
        ts = _station_last_obs_timestamp(txt)
        if ts is None or ts < stale_cutoff:
            stale.add(str(cod))
    return stale


def _bndmet_merge_accum_stale_last_obs(
    primary: tuple[dict[str, float], set[str], dict[str, str]],
    secondary: tuple[dict[str, float], set[str], dict[str, str]],
    *,
    stale_cutoff: pd.Timestamp,
) -> tuple[dict[str, float], set[str], dict[str, str]]:
    """
    Une BQ + CSV: acumulado do primário (BQ) com fallback do secundário;
    ``last_obs`` usa o timestamp mais recente entre as duas fontes.
    """
    accum_p, _stale_p, lo_p = primary
    accum_s, _stale_s, lo_s = secondary
    accum = dict(accum_p)
    for cod, mm in accum_s.items():
        cod = str(cod)
        try:
            mm_s = float(mm)
        except (TypeError, ValueError):
            continue
        if cod not in accum:
            accum[cod] = mm_s
            continue
        try:
            mm_p = float(accum[cod])
        except (TypeError, ValueError):
            accum[cod] = mm_s
            continue
        # Preferir o maior acumulado (CSV/horario zerado não deve tapar clima_series).
        if mm_s > mm_p:
            accum[cod] = mm_s
    last_obs = dict(lo_p)
    for cod, txt in lo_s.items():
        cod = str(cod)
        ts_new = _station_last_obs_timestamp(txt)
        ts_old = _station_last_obs_timestamp(last_obs.get(cod))
        if ts_new is not None and (ts_old is None or ts_new > ts_old):
            last_obs[cod] = txt
    stale = _bndmet_stale_codes_from_last_obs(last_obs, stale_cutoff)
    return accum, stale, last_obs


def _bndmet_accum_from_csv_horario(
    path: Path,
    *,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    stale_cutoff: pd.Timestamp,
) -> tuple[dict[str, float], set[str], dict[str, str]]:
    if not path.exists():
        return {}, set(), {}
    try:
        df = pd.read_csv(path, encoding="utf-8")
        df = _normalize_bndmet_dados_columns(df)
        if df.empty or "codFenomeno" not in df.columns:
            return {}, set(), {}
        return _bndmet_accum_last_obs_from_horario_df(
            df, start_ts=start_ts, end_ts=end_ts, stale_cutoff=stale_cutoff
        )
    except Exception:
        return {}, set(), {}


def get_bndmet_accum_hours_stale(
    ref_date: str,
    *,
    hours: int = 120,
    stale_days: int = 3,
    bq_dataset: str | None = None,
    bq_table: str = "bndmet_horario",
    csv_path: Path | None = None,
) -> tuple[dict[str, float], set[str], dict[str, str]]:
    """
    Acumulado de precipitação I175 nas últimas ``hours`` horas até o fim do dia UTC de ``ref_date``
    (limitado ao instante atual se o dia ainda não passou), mais conjunto de estações **sem dados**
    nos últimos ``stale_days`` dias (cinza no mapa) e último ``dataHora`` por estação (rótulo).

    Ordem: BigQuery ``bndmet_horario`` (se existir) + CSV local; fallback/merge ``clima_series``.
    """
    accum: dict[str, float] = {}
    last_obs: dict[str, str] = {}
    stale: set[str] = set()

    ref_day = pd.Timestamp(ref_date).normalize().tz_localize("UTC")
    ref_end = ref_day + pd.Timedelta(days=1)
    now = pd.Timestamp.now(tz="UTC")
    end_ts = min(ref_end, now)
    start_ts = end_ts - pd.Timedelta(hours=int(hours))
    # Recência relativa ao dia selecionado — evita mapa todo cinza em datas passadas.
    stale_cutoff = end_ts - pd.Timedelta(days=int(stale_days))

    # —— BigQuery ——
    if bq_dataset and _bndmet_horario_bq_available(bq_dataset, bq_table):
        try:
            from gcp.bq_config import get_bq_fully_qualified_prefix
            from gcp.bq_load_clima import _bq_client

            client = _bq_client()
            if client is not None:
                _pfx = get_bq_fully_qualified_prefix(bq_dataset)
                full_h = f"{_pfx}.{bq_table}"
                sql_acc = f"""
                SELECT
                  CAST(codEstacao AS STRING) AS cod,
                  COALESCE(SUM(SAFE_CAST(valor AS FLOAT64)), 0) AS mm
                FROM `{full_h}`
                WHERE codFenomeno = @fen
                  AND dataHora >= @t_start
                  AND dataHora < @t_end
                GROUP BY codEstacao
                """
                sql_last = f"""
                SELECT CAST(codEstacao AS STRING) AS cod, MAX(dataHora) AS last_ts
                FROM `{full_h}`
                WHERE dataHora < @t_end
                GROUP BY codEstacao
                """
                try:
                    from google.cloud import bigquery

                    cfg_acc = bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter("fen", "STRING", BNDMET_PRECIP_COD),
                            bigquery.ScalarQueryParameter("t_start", "TIMESTAMP", start_ts.to_pydatetime()),
                            bigquery.ScalarQueryParameter("t_end", "TIMESTAMP", end_ts.to_pydatetime()),
                        ]
                    )
                    df_a = client.query(sql_acc, job_config=cfg_acc).to_dataframe()
                    if df_a is not None and not df_a.empty:
                        for _, row in df_a.iterrows():
                            rd = {str(k).lower(): v for k, v in row.items()}
                            cod = str(rd.get("cod") or "").strip()
                            if not cod:
                                continue
                            accum[cod] = float(rd.get("mm", 0.0))

                    cfg_last = bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter(
                                "t_end", "TIMESTAMP", end_ts.to_pydatetime()
                            ),
                        ]
                    )
                    df_l = client.query(sql_last, job_config=cfg_last).to_dataframe()
                    if df_l is not None and not df_l.empty:
                        for _, row in df_l.iterrows():
                            rd = {str(k).lower(): v for k, v in row.items()}
                            cod = str(rd.get("cod") or "").strip()
                            tsv = _bndmet_horario_ts_utc(rd.get("last_ts"))
                            if not cod or tsv is None:
                                continue
                            last_obs[cod] = tsv.strftime("%Y-%m-%d %H:%M UTC")
                            if tsv < stale_cutoff:
                                stale.add(cod)
                except Exception:
                    pass
        except Exception:
            pass

    bq_result = (accum, stale, last_obs)
    path = csv_path or BNDMET_DADOS_CSV
    csv_result = _bndmet_accum_from_csv_horario(
        path, start_ts=start_ts, end_ts=end_ts, stale_cutoff=stale_cutoff
    )
    if not bq_result[2] and not csv_result[2]:
        hourly = ({}, set(), {})
    elif not bq_result[2]:
        hourly = csv_result
    elif not csv_result[2]:
        hourly = bq_result
    else:
        hourly = _bndmet_merge_accum_stale_last_obs(
            bq_result, csv_result, stale_cutoff=stale_cutoff
        )

    # Fallback/merge: clima_series BNDMET only — Orion/EWS tem caminho próprio (não rotular como BNDMET).
    ds = bq_dataset or os.environ.get("BQ_DATASET") or "efc_clima"
    series = _station_accum_stale_from_clima_series(
        ref_date,
        hours=hours,
        stale_days=stale_days,
        sources=("estacoes", "bndmet"),
        dataset=ds,
    )
    if not hourly[2]:
        return series if series[2] else ({}, set(), {})
    if not series[2]:
        return hourly
    return _bndmet_merge_accum_stale_last_obs(hourly, series, stale_cutoff=stale_cutoff)


def get_bndmet_daily_precip_by_station(csv_path: Path = None, last_n_days: int = 7):
    """
    Precipitação diária por estação BNDMET (codEstacao).
    Retorna dict: cod_estacao -> list of (date_str, mm) ordenado por data.
    """
    path = csv_path or BNDMET_DADOS_CSV
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, encoding="utf-8")
        df = _normalize_bndmet_dados_columns(df)
        if df.empty or "codFenomeno" not in df.columns or "codEstacao" not in df.columns or "dataHora" not in df.columns or "valor" not in df.columns:
            return {}
        df = df[df["codFenomeno"].astype(str) == BNDMET_PRECIP_COD].copy()
        df["_dt"] = pd.to_datetime(df["dataHora"], errors="coerce")
        df = df.dropna(subset=["_dt"])
        df["_valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0)
        df["_date"] = df["_dt"].dt.date
        cutoff = (pd.Timestamp.now().normalize() - pd.Timedelta(days=last_n_days)).date()
        df = df[df["_date"] >= cutoff]
        daily = df.groupby(["codEstacao", "_date"])["_valor"].sum().reset_index()
        daily["codEstacao"] = daily["codEstacao"].astype(str)
        out = {}
        for cod in daily["codEstacao"].unique():
            sub = daily[daily["codEstacao"] == cod].sort_values("_date")
            out[cod] = [(pd.Timestamp(d).strftime("%Y-%m-%d"), round(float(v), 2)) for d, v in zip(sub["_date"], sub["_valor"])]
        return out
    except Exception:
        return {}


def get_bndmet_daily_precip_by_station_from_bq(dataset: str = "efc_clima", table: str = "bndmet_horario", last_n_days: int = 7):
    """
    Versão BQ: precipitação diária por estação BNDMET (codEstacao) diretamente do BigQuery.
    Retorna dict: cod_estacao -> list of (date_str, mm) ordenado por data.
    Se BigQuery não estiver disponível ou falhar, retorna {}.
    """
    try:
        from gcp.bq_reader_clima import load_bndmet_daily_from_bq
    except ImportError:
        return {}
    try:
        df = load_bndmet_daily_from_bq(dataset=dataset, table=table, days=last_n_days)
    except Exception:
        return {}
    if df is None or df.empty or "codEstacao" not in df.columns or "data" not in df.columns or "precip_mm" not in df.columns:
        return {}
    df = df.copy()
    df["codEstacao"] = df["codEstacao"].astype(str)
    df["data"] = pd.to_datetime(df["data"]).dt.date
    out: dict[str, list[tuple[str, float]]] = {}
    for cod in df["codEstacao"].unique():
        sub = df[df["codEstacao"] == cod].sort_values("data")
        out[cod] = [(pd.Timestamp(d).strftime("%Y-%m-%d"), round(float(v), 2)) for d, v in zip(sub["data"], sub["precip_mm"])]
    return out


def sample_suscetibilidade_at_points(lats, lons, tif_path: Path = None) -> list:
    """
    Amostra o raster de suscetibilidade nos pontos (lat, lon). Retorna lista de float (um por ponto);
    None ou np.nan onde o ponto está fora do raster ou em falha.
    Útil para preencher indice_suscetibilidade por km no BigQuery.
    """
    path = tif_path or SUSCETIBILIDADE_TIF
    if path and not path.exists():
        folder = (tif_path or SUSCETIBILIDADE_TIF).parent
        if folder.exists():
            candidates = list(folder.glob("*.tif")) + list(folder.glob("*.tiff"))
            path = candidates[0] if candidates else None
    if not path or not path.exists():
        return [None] * max(len(lats), len(lons), 1)
    try:
        import rasterio
        from rasterio.warp import transform
        from rasterio.crs import CRS
    except ImportError:
        return [None] * max(len(lats), len(lons), 1)
    try:
        with rasterio.open(path) as src:
            wgs = CRS.from_epsg(4326)
            if src.crs != wgs:
                xs, ys = transform(wgs, src.crs, list(lons), list(lats))
            else:
                xs, ys = list(lons), list(lats)
            out = []
            for x, y in zip(xs, ys):
                try:
                    # sample() espera (x, y) no CRS do dataset; retorna gerador de valores por banda
                    vals = list(src.sample([(x, y)]))
                    v = float(vals[0][0]) if vals and len(vals[0]) > 0 else None
                    if v is not None and (np.isnan(v) or (getattr(src, "nodata", None) is not None and v == src.nodata)):
                        v = None
                    out.append(v)
                except Exception:
                    out.append(None)
            return out
    except Exception:
        return [None] * max(len(lats), len(lons), 1)


# Cache do raster de suscetibilidade: carregado 1x e reutilizado em todos os mapas (evita OOM)
_SUSCETIBILIDADE_CACHE = {}


def _station_accum_legend_html(vmax: float, *, accum_hours: int = 120) -> str:
    vmax = max(1.0, float(vmax))
    mid = vmax / 2.0
    h = int(accum_hours)
    title = f"BNDMET / ANA — acumulado {h} h (mm)"
    return (
        '<div style="margin:10px 8px 0 8px;padding:8px 12px 4px 12px;'
        'background:rgba(15,23,42,0.94);border:1px solid rgba(71,85,105,0.45);'
        'border-radius:10px;">'
        '<div style="font:600 12px/1.2 system-ui,-apple-system,Segoe UI,sans-serif;'
        f'color:#e2e8f0;margin-bottom:6px;">{title}</div>'
        '<div style="height:10px;border-radius:999px;'
        'background:linear-gradient(90deg,#f8fafc 0%,#93c5fd 34%,#2563eb 68%,#1e1b4b 100%);"></div>'
        '<div style="display:flex;justify-content:space-between;'
        'font:500 11px/1.2 system-ui,-apple-system,Segoe UI,sans-serif;'
        'color:#cbd5e1;margin-top:5px;">'
        f"<span>0</span><span>{mid:.0f}</span><span>{vmax:.0f}</span>"
        "</div></div>"
    )

def _load_suscetibilidade_from_png(max_pixels: int | None = None):
    """
    Fallback sem rasterio: PNG pré-renderizado + bounds em suscetibilidade.json.
    """
    import json

    png_candidates = [
        # Prod monta o PNG dilatado aqui (GCS FUSE). Preferir essa cópia.
        DATA_DIR / "geotec_geojson_fallback" / "suscetibilidade-overlay.png",
        SUSCETIBILIDADE_TIF.parent / "suscetibilidade-overlay.png",
    ]
    png_path = next((p for p in png_candidates if p.exists()), None)
    meta_path = DATA_DIR / "geotec_geojson_fallback" / "suscetibilidade.json"
    if not png_path or not meta_path.exists():
        return None, None
    if max_pixels is None:
        max_pixels = int(os.environ.get("SUSCETIBILIDADE_MAX_PX", "256")) or 256
    cache_key = (str(png_path.resolve()), max_pixels or 0, "png_v3_tif")
    if cache_key in _SUSCETIBILIDADE_CACHE:
        return _SUSCETIBILIDADE_CACHE[cache_key]
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        b = meta.get("bounds") or {}
        south, west, north, east = float(b["south"]), float(b["west"]), float(b["north"]), float(b["east"])
        bounds_folium = [[south, west], [north, east]]
    except Exception:
        return None, None
    try:
        import matplotlib.image as mpimg

        arr = mpimg.imread(str(png_path))
        if arr.dtype != np.uint8:
            arr = (np.clip(arr, 0.0, 1.0) * 255.0).astype(np.uint8)
        if arr.ndim == 2:
            rgb = np.stack([arr, arr, arr], axis=-1)
            alpha = np.where(arr > 0, 220, 0).astype(np.uint8)
            rgba_uint8 = np.dstack([rgb, alpha])
        elif arr.shape[2] == 3:
            alpha = np.full(arr.shape[:2] + (1,), 220, dtype=np.uint8)
            rgba_uint8 = np.concatenate([arr, alpha], axis=2)
        else:
            rgba_uint8 = arr[:, :, :4].copy()
            # Folium aplica opacity em cima do alpha — pixels de dados precisam alpha cheio.
            data_mask = np.any(rgba_uint8[:, :, :3] > 8, axis=2)
            rgba_uint8[:, :, 3] = np.where(data_mask, 255, 0).astype(np.uint8)
        h, w = rgba_uint8.shape[0], rgba_uint8.shape[1]
        if max_pixels and max(h, w) > max_pixels:
            step = max(1, max(h, w) // max_pixels)
            rgba_uint8 = rgba_uint8[::step, ::step].copy()
        result = (rgba_uint8, bounds_folium)
        _SUSCETIBILIDADE_CACHE[cache_key] = result
        return result
    except Exception:
        return None, None


def _load_suscetibilidade_raster(tif_path: Path = None, max_pixels: int = None):
    """
    Carrega o GeoTIFF de suscetibilidade e retorna (image_rgba, bounds) para folium.ImageOverlay.
    bounds = [[south, west], [north, east]] em WGS84.
    Resultado é cacheado por path para não recarregar a cada mapa.
    max_pixels: se definido (ex: 512), reduz o lado maior da imagem para esse tamanho (economia de memória).
    Retorna (None, None) se rasterio não estiver instalado ou o arquivo não existir.
    """
    import os
    path = tif_path or SUSCETIBILIDADE_TIF
    if path and not path.exists():
        folder = (tif_path or SUSCETIBILIDADE_TIF).parent
        if folder.exists():
            candidates = list(folder.glob("*.tif")) + list(folder.glob("*.tiff"))
            path = candidates[0] if candidates else None
    if not path or not path.exists():
        return None, None
    # Limite padrão 256 px para não estourar MessageSizeError do Streamlit (200 MB)
    if max_pixels is None:
        max_pixels = int(os.environ.get("SUSCETIBILIDADE_MAX_PX", "256")) or 256
    cache_key = (str(path.resolve()), max_pixels or 0, "v8_warp_band")
    if cache_key in _SUSCETIBILIDADE_CACHE:
        return _SUSCETIBILIDADE_CACHE[cache_key]
    try:
        import rasterio
        from rasterio.warp import transform_bounds, reproject, Resampling, calculate_default_transform
        from rasterio.transform import array_bounds
        from rasterio.crs import CRS
    except ImportError:
        return None, None
    try:
        with rasterio.open(path) as src:
            crs_wgs = CRS.from_epsg(4326)
            bounds_wgs = transform_bounds(src.crs, crs_wgs, *src.bounds)
            west, south, east, north = bounds_wgs[0], bounds_wgs[1], bounds_wgs[2], bounds_wgs[3]
            if south > north:
                south, north = north, south
            if west > east:
                west, east = east, west
            if int(src.count) < 1:
                return None, None
            nodata = getattr(src, "nodata", None)
            dst_w = max(64, int(max_pixels))
            dst_h = max(64, int(round(dst_w * src.height / max(src.width, 1))))
            transform, width, height = calculate_default_transform(
                src.crs,
                crs_wgs,
                src.width,
                src.height,
                *src.bounds,
                dst_width=dst_w,
                dst_height=dst_h,
            )
            band = np.empty((height, width), dtype=np.float64)
            reproject(
                source=rasterio.band(src, 1),
                destination=band,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=crs_wgs,
                resampling=Resampling.bilinear,
                src_nodata=nodata if nodata is not None else np.nan,
                dst_nodata=np.nan,
            )
            valid = np.isfinite(band) & (band > 0)
            if not np.any(valid):
                return None, None
            vals = band[valid]
            lo, hi = np.percentile(vals, [5.0, 97.0])
            if hi <= lo:
                lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
            if hi <= lo:
                hi = lo + 1.0
            norm = (band - lo) / (hi - lo)
            norm = np.clip(norm, 0, 1)
            norm = np.where(valid, np.power(norm, 0.82), np.nan)
            try:
                import matplotlib

                matplotlib.use("Agg")
                import matplotlib.colors as mcolors

                cmap = mcolors.LinearSegmentedColormap.from_list(
                    "suscetibilidade_monitor",
                    ["#0f766e", "#84cc16", "#fde047", "#fb923c", "#b91c1c"],
                )
                rgba = cmap(norm)
                rgba_uint8 = (rgba[:, :, :4] * 255).astype(np.uint8)
                rgba_uint8[:, :, 3] = np.where(valid, 255, 0).astype(np.uint8)
            except Exception:
                return None, None
    except Exception:
        return None, None
    west, south, east, north = array_bounds(height, width, transform)
    bounds_folium = [[south, west], [north, east]]
    result = (rgba_uint8, bounds_folium)
    _SUSCETIBILIDADE_CACHE[cache_key] = result
    return result


def _suscetibilidade_map_mode() -> str:
    """rail = faixa colorida sobre o eixo ferroviário; raster = ImageOverlay georreferenciado."""
    return os.environ.get("SUSCETIBILIDADE_MAP_MODE", "rail").strip().lower()


def _suscetibilidade_colormap():
    import matplotlib.colors as mcolors

    return mcolors.LinearSegmentedColormap.from_list(
        "suscetibilidade_monitor",
        ["#0f766e", "#84cc16", "#fde047", "#fb923c", "#b91c1c"],
    )


def _suscetibilidade_color_hex(val, lo: float, hi: float) -> str | None:
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v) or v <= 0:
        return None
    if hi <= lo:
        hi = lo + 1.0
    norm = float(np.clip((v - lo) / (hi - lo), 0.0, 1.0) ** 0.82)
    rgba = _suscetibilidade_colormap()(norm)
    import matplotlib.colors as mcolors

    return mcolors.to_hex(rgba[:3])


_SUSC_BAND_HEX = ("#0f766e", "#84cc16", "#fde047", "#fb923c", "#b91c1c")


def _suscetibilidade_band_hex(val, lo: float, hi: float) -> str | None:
    """5 cores da legenda (em vez de contínuo) — trechos iguais se fundem, mapa mais leve."""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v) or v <= 0:
        return None
    if hi <= lo:
        hi = lo + 1.0
    t = float(np.clip((v - lo) / (hi - lo), 0.0, 1.0) ** 0.82)
    idx = min(len(_SUSC_BAND_HEX) - 1, int(t * len(_SUSC_BAND_HEX)))
    return _SUSC_BAND_HEX[idx]


def _coalesce_colored_segments(
    chain: list[tuple[float, float]],
    colors: list[str | None],
) -> list[tuple[str, list[tuple[float, float]]]]:
    """Junta vértices consecutivos da mesma cor numa polyline só."""
    runs: list[tuple[str, list[tuple[float, float]]]] = []
    cur_color: str | None = None
    cur_pts: list[tuple[float, float]] = []
    for a, b, color in zip(chain, chain[1:], colors):
        if not color:
            if cur_pts and cur_color:
                runs.append((cur_color, cur_pts))
            cur_color = None
            cur_pts = []
            continue
        if color == cur_color:
            cur_pts.append(b)
        else:
            if cur_pts and cur_color:
                runs.append((cur_color, cur_pts))
            cur_pts = [a, b]
            cur_color = color
    if cur_pts and cur_color:
        runs.append((cur_color, cur_pts))
    return runs


def _add_suscetibilidade_railway_layer(
    m, main_line, cumulative_km, *, show: bool = False
) -> bool:
    """
    Desenha suscetibilidade como segmentos coloridos sobre o eixo da ferrovia
    (mesma geometria do traçado vermelho — evita deslocamento do raster reamostrado).
    """
    import folium

    if main_line is None or cumulative_km is None or len(main_line) < 2:
        return False
    try:
        step_km = max(0.2, float(os.environ.get("SUSCETIBILIDADE_LINE_STEP_KM", "0.4")))
        line_weight = max(3.0, min(14.0, float(os.environ.get("SUSCETIBILIDADE_LINE_WEIGHT", "6"))))
        opacity = _suscetibilidade_map_opacity()
    except (TypeError, ValueError):
        step_km, line_weight, opacity = 0.4, 6.0, 0.55

    cum = np.asarray(cumulative_km, dtype=float)
    km_end = float(cum[-1])
    km_start = float(cum[0])
    chain: list[tuple[float, float]] = []
    km = km_start
    while km <= km_end + 1e-6:
        pos = position_at_km(km, main_line, cumulative_km)
        if pos is not None:
            chain.append(pos)
        km += step_km
    if len(chain) < 2:
        return False

    mids_lat: list[float] = []
    mids_lon: list[float] = []
    for a, b in zip(chain, chain[1:]):
        mids_lat.append((a[0] + b[0]) * 0.5)
        mids_lon.append((a[1] + b[1]) * 0.5)
    vals = sample_suscetibilidade_at_points(mids_lat, mids_lon)
    valid = [float(v) for v in vals if v is not None and np.isfinite(v) and float(v) > 0]
    if not valid:
        return False
    lo, hi = np.percentile(valid, [5.0, 97.0])
    if hi <= lo:
        lo, hi = float(min(valid)), float(max(valid))
    if hi <= lo:
        hi = lo + 1.0

    try:
        from folium.map import CustomPane

        if not any(getattr(c, "name", None) == "efcSuscPane" for c in getattr(m, "_children", {}).values()):
            CustomPane("efcSuscPane", z_index=350).add_to(m)
    except ImportError:
        pass

    fg = folium.FeatureGroup(name="Suscetibilidade", show=bool(show), control=False)
    colors = [_suscetibilidade_band_hex(val, lo, hi) for val in vals]
    for color, pts in _coalesce_colored_segments(chain, colors):
        if len(pts) < 2:
            continue
        folium.PolyLine(
            locations=pts,
            color=color,
            weight=line_weight,
            opacity=opacity,
            line_cap="round",
            line_join="round",
            interactive=False,
            pane="efcSuscPane",
        ).add_to(fg)
    fg.add_to(m)
    # Espessura fixa em px (como suscetibilidade_preview_efc — linha fina sobre o eixo).
    wt = int(round(line_weight))
    m.get_root().html.add_child(
        folium.Element(
            "<script>(function(){"
            f"var WT={wt};"
            "function findMap(){if(!window.L)return null;"
            'var el=document.querySelector(".folium-map");'
            'if(el&&el.id&&window[el.id]&&typeof window[el.id].addLayer==="function")return window[el.id];'
            'var k;for(k in window){if(k.indexOf("map_")===0&&window[k]&&typeof window[k].addLayer==="function")return window[k];}'
            "return null;}"
            "function apply(map){"
            "map.eachLayer(function walk(layer){if(layer.eachLayer){layer.eachLayer(walk);return;}"
            'if(layer.setStyle&&layer.options&&layer.options.pane==="efcSuscPane")'
            "layer.setStyle({weight:WT});});}"
            "function boot(){var m=findMap();if(!m){setTimeout(boot,120);return;}apply(m);}"
            "boot();})();</script>"
        )
    )
    return True


def _add_suscetibilidade_raster_layer(
    m, main_line, *, max_pixels: int, show: bool = False
) -> bool:
    """Camada raster legada (ImageOverlay) — pode desalinhar em zoom alto."""
    import folium

    img_rgba, bounds_susc = load_suscetibilidade_raster_for_map(max_pixels=max_pixels)
    if img_rgba is None or bounds_susc is None:
        return False
    if main_line is not None and len(main_line) >= 2:
        img_rgba = _mask_suscet_rgba_to_corridor(img_rgba, bounds_susc, main_line)
    folium.raster_layers.ImageOverlay(
        image=img_rgba,
        bounds=bounds_susc,
        name="Suscetibilidade",
        overlay=True,
        control=False,
        show=bool(show),
        opacity=_suscetibilidade_map_opacity(),
        interactive=True,
        cross_origin=False,
        zindex=1,
    ).add_to(m)
    return True


def _suscetibilidade_corridor_km() -> float:
    """Largura da máscara raster (km a partir do eixo). Default = buffer em metros."""
    try:
        raw = (os.environ.get("SUSCETIBILIDADE_CORRIDOR_KM") or "").strip()
        if raw:
            return max(0.05, float(raw))
        return max(0.05, _suscetibilidade_buffer_m() / 1000.0)
    except (TypeError, ValueError):
        return 40.0


def _mask_suscet_rgba_to_corridor(
    rgba_uint8: np.ndarray,
    bounds_folium: list,
    main_line,
    *,
    buffer_km: float | None = None,
) -> np.ndarray:
    """Mantém suscetibilidade só numa faixa (buffer) ao longo da ferrovia — evita tingir o mapa todo."""
    if rgba_uint8 is None or main_line is None or len(main_line) < 2:
        return rgba_uint8
    # PNG já vem em corredor / skip via env (evita remask destrutivo em baixa res).
    skip = os.environ.get("SUSCETIBILIDADE_SKIP_CORRIDOR_MASK", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if skip:
        return rgba_uint8
    buffer_km = float(buffer_km if buffer_km is not None else _suscetibilidade_corridor_km())
    south, west = float(bounds_folium[0][0]), float(bounds_folium[0][1])
    north, east = float(bounds_folium[1][0]), float(bounds_folium[1][1])
    h, w = rgba_uint8.shape[0], rgba_uint8.shape[1]
    if h < 2 or w < 2:
        return rgba_uint8
    # Folium ImageOverlay / PNG: row 0 = north (topo). Usar south→north invertia o
    # corredor e zerava o alpha na prática (ex.: 5 px visíveis a 256px).
    lats = np.linspace(north, south, h)
    lons = np.linspace(west, east, w)
    has_data = rgba_uint8[:, :, 3] > 0
    ys, xs = np.where(has_data)
    out = rgba_uint8.copy()
    if ys.size == 0:
        return out
    rail = np.asarray(main_line, dtype=float)
    step = max(1, len(rail) // 500)
    rail = rail[::step]
    rail_lats = np.radians(rail[:, 0])
    rail_lons = np.radians(rail[:, 1])
    # Só pixels com dados (PNG já é esparso) — evita tensor (H,W,N) que estoura RAM no Cloud Run.
    lat_rad = np.radians(lats[ys])[:, None]
    lon_rad = np.radians(lons[xs])[:, None]
    dlat = rail_lats[None, :] - lat_rad
    dlon = rail_lons[None, :] - lon_rad
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat_rad) * np.cos(rail_lats[None, :]) * np.sin(dlon / 2.0) ** 2
    min_dist = (6371.0 * 2.0 * np.arcsin(np.sqrt(np.minimum(a, 1.0)))).min(axis=1)
    feather_km = max(0.02, min(buffer_km * 0.35, 0.7))
    fade = np.clip((buffer_km - min_dist) / max(feather_km, 0.02), 0.0, 1.0)
    fade = np.where(min_dist <= buffer_km, fade, 0.0)
    alpha = out[:, :, 3].astype(np.float64)
    alpha[ys, xs] = 255.0 * fade
    out[:, :, 3] = alpha.astype(np.uint8)
    return out


def load_suscetibilidade_raster_for_map(tif_path: Path = None, max_pixels: int = None):
    """GeoTIFF completo (reprojeta via rasterio.band) ou PNG pré-renderizado."""
    prefer_png = os.environ.get("SUSCETIBILIDADE_PREFER_PNG", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if prefer_png:
        out = _load_suscetibilidade_from_png(max_pixels)
        if out[0] is not None:
            return out
    out = _load_suscetibilidade_raster(tif_path, max_pixels)
    if out[0] is not None:
        return out
    return _load_suscetibilidade_from_png(max_pixels)


_KM_MARKER_STYLES = {
    "100": {"font": "12px", "bg": "rgba(148,163,184,0.92)", "border": "1px solid rgba(71,85,105,0.85)", "color": "#0f172a", "pad": "3px 7px", "radius": "6px", "weight": "600", "shadow": "0 1px 3px rgba(0,0,0,.2)"},
    "50":  {"font": "11px", "bg": "rgba(125,211,252,0.35)", "border": "1px solid rgba(56,189,248,0.45)", "color": "#e0f2fe", "pad": "2px 6px", "radius": "5px", "weight": "600", "shadow": "0 1px 2px rgba(0,0,0,.18)"},
    "10":  {"font": "10px", "bg": "rgba(30,41,59,0.75)", "border": "1px solid rgba(100,116,139,0.6)", "color": "#e2e8f0", "pad": "2px 5px", "radius": "4px", "weight": "500", "shadow": "0 1px 2px rgba(0,0,0,.12)"},
}

def _add_zoom_markers(m, mk_dict: dict):
    """Adiciona markers de km ao mapa Folium com visibilidade dependente do nível de zoom.
    mk_dict = {"100": [(km, lat, lon), ...], "50": [...], "10": [...]}.
    Zoom <8 → só 100 km; 8-9 → 50 km; >=10 → 10 km."""
    import folium

    layer_cfg = [
        ("100", 0, 99),
        ("50",  8, 99),
        ("10",  10, 99),
    ]
    fg_objs = []
    for key, min_zoom, _ in layer_cfg:
        pts = mk_dict.get(key, [])
        if not pts:
            continue
        already = set()
        if key == "50":
            already = {km for km, _, _ in mk_dict.get("100", [])}
        elif key == "10":
            already = {km for km, _, _ in mk_dict.get("100", [])} | {km for km, _, _ in mk_dict.get("50", [])}
        sty = _KM_MARKER_STYLES.get(key, _KM_MARKER_STYLES["10"])
        fg = folium.FeatureGroup(name=f"Km ({key})", show=(min_zoom == 0))
        for km, lat_m, lon_m in pts:
            if km in already:
                continue
            html = (
                f'<div style="font-size:{sty["font"]};font-weight:{sty["weight"]};'
                f'background:{sty["bg"]};border:{sty["border"]};padding:{sty["pad"]};'
                f'border-radius:{sty["radius"]};color:{sty["color"]};'
                f'box-shadow:{sty["shadow"]};white-space:nowrap;text-align:center;'
                f'pointer-events:auto;cursor:default;">{km} km</div>'
            )
            folium.Marker(
                [lat_m, lon_m],
                icon=folium.DivIcon(html=html, icon_size=(None, None), icon_anchor=(0, 0)),
                popup=f"km {km}",
            ).add_to(fg)
        fg.add_to(m)
        fg_objs.append((fg, min_zoom))

    if not fg_objs:
        return

    from jinja2 import Template as JTemplate
    map_name = m.get_name()
    js_entries = []
    for fg, min_zoom in fg_objs:
        js_entries.append(f'      {{ layer: {fg.get_name()}, minZoom: {min_zoom} }}')
    js_array = ",\n".join(js_entries)

    class _ZoomHandler(folium.MacroElement):
        _template = JTemplate(
            "{% macro script(this, kwargs) %}\n"
            "    (function() {\n"
            "      var map = " + map_name + ";\n"
            "      var configs = [\n"
            + js_array + "\n"
            "      ];\n"
            "      function updateVisibility() {\n"
            "        var z = map.getZoom();\n"
            "        configs.forEach(function(c) {\n"
            "          if (z >= c.minZoom) {\n"
            "            if (!map.hasLayer(c.layer)) map.addLayer(c.layer);\n"
            "          } else {\n"
            "            if (map.hasLayer(c.layer)) map.removeLayer(c.layer);\n"
            "          }\n"
            "        });\n"
            "      }\n"
            "      map.on('zoomend', updateVisibility);\n"
            "      updateVisibility();\n"
            "    })();\n"
            "{% endmacro %}"
        )

    _ZoomHandler().add_to(m)


_STATION_COLOR_RAIN = ("#2980b9", "#3498db")    # azul (border, fill) — legado 24 h
_STATION_COLOR_DRY = ("#27ae60", "#2ecc71")    # verde — legado 24 h
_STATION_COLOR_STALE = ("#94a3b8", "#cbd5e1")    # cinza suave — sem dados recentes
_STATION_COLOR_NO_RAIN_120H = ("#a1a1aa", "#d4d4d8")  # cinza suave — dados ok mas sem chuva nas últimas 120 h
_ANA_STATION_COLOR = ("#6d28d9", "#a78bfa")
_TRANSPARENT_FILL = "#000000"
_STATION_HIDE_AFTER_DAYS = 30

# Estilo dos pontos por km na ferrovia (hierarquia: hub > major > tick)
_KM_POINT_RADIUS = 2.4
_KM_POINT_COLOR = "#475569"
_KM_POINT_FILL = "#94a3b8"
_KM_POINT_STEP = 2  # default; no mapa operacional pode usar override
_KM_POINT_MAJOR_EVERY = 50
_KM_POINT_HUB_EVERY = 100
# Ferrovia: casco escuro + trilho claro (legível em mapa claro ou escuro)
_RAILWAY_LINE_COLOR = "#e2e8f0"
_RAILWAY_LINE_WEIGHT = 2.4
_RAILWAY_LINE_OPACITY = 0.96
_RAILWAY_CASING_COLOR = "#0f172a"
_RAILWAY_CASING_WEIGHT = 6.2
_RAILWAY_CASING_OPACITY = 0.62
_RAILWAY_CORE_COLOR = "#f8fafc"
_RAILWAY_CORE_WEIGHT = 1.05
_RAILWAY_CORE_OPACITY = 0.55


def _draw_railway_polyline(m, lines, *, line_color=None, line_weight=None, popup: str = "Linha EFC"):
    """Desenha ferrovia com casco + trilho + filete (hierarquia visual limpa)."""
    import folium

    color = line_color or _RAILWAY_LINE_COLOR
    weight = float(line_weight) if line_weight is not None else _RAILWAY_LINE_WEIGHT
    for line in lines:
        if not line or len(line) < 2:
            continue
        folium.PolyLine(
            locations=line,
            color=_RAILWAY_CASING_COLOR,
            weight=max(_RAILWAY_CASING_WEIGHT, weight + 3.5),
            opacity=_RAILWAY_CASING_OPACITY,
            interactive=False,
        ).add_to(m)
        folium.PolyLine(
            locations=line,
            color=color,
            weight=weight,
            opacity=_RAILWAY_LINE_OPACITY,
            popup=popup,
        ).add_to(m)
        folium.PolyLine(
            locations=line,
            color=_RAILWAY_CORE_COLOR,
            weight=max(0.8, weight * 0.42),
            opacity=_RAILWAY_CORE_OPACITY,
            interactive=False,
        ).add_to(m)


def _railway_bounds_points(main_line, all_lines, *, prefer_all_lines: bool = False) -> list[tuple[float, float]]:
    """Pontos (lat, lon) para enquadrar o mapa na ferrovia."""
    sources: list = []
    if prefer_all_lines and all_lines:
        sources = list(all_lines)
    elif main_line and len(main_line) >= 2:
        sources = [main_line]
    elif all_lines:
        sources = list(all_lines)
    pts: list[tuple[float, float]] = []
    for line in sources:
        for p in line:
            pts.append((float(p[0]), float(p[1])))
    return pts


def _apply_railway_fit_bounds(m, main_line, all_lines, *, fit_main_line_bounds: bool) -> None:
    if not fit_main_line_bounds:
        return
    lines = _filter_efc_railway_lines(list(all_lines or []))
    if not lines and main_line:
        lines = [main_line]
    pts = _railway_bounds_points(main_line, lines, prefer_all_lines=True)
    if len(pts) < 2:
        return
    import os

    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    pad = float(os.environ.get("EFC_MAP_FIT_PADDING_DEG", "0.06"))
    lat_lo = max(min(lats) - pad, LAT_MIN_EFC - 0.15)
    lat_hi = min(max(lats) + pad, LAT_MAX_EFC + 0.15)
    lon_lo = max(min(lons) - pad, LON_MIN_EFC - 0.15)
    lon_hi = min(max(lons) + pad, LON_MAX_EFC + 0.15)
    bounds = [[lat_lo, lon_lo], [lat_hi, lon_hi]]
    m.fit_bounds(bounds)
    try:
        pad_bounds = float(os.environ.get("EFC_MAP_MAXBOUNDS_PADDING_DEG", "0.14"))
        m.options["maxBounds"] = [
            [lat_lo - pad_bounds, lon_lo - pad_bounds],
            [lat_hi + pad_bounds, lon_hi + pad_bounds],
        ]
        m.options["maxBoundsViscosity"] = float(os.environ.get("EFC_MAP_MAXBOUNDS_VISCOSITY", "0.35"))
        m.options["wheelPxPerZoomLevel"] = int(os.environ.get("EFC_MAP_WHEEL_PX_PER_ZOOM", "72"))
    except Exception:
        pass


def _add_railway_km_points(m, main_line, cumulative_km, step_km: int = None):
    """Marcadores de km com hierarquia: hub (100) > major (50) > tick."""
    import folium
    if main_line is None or cumulative_km is None or len(main_line) < 2 or len(cumulative_km) < 2:
        return
    step = step_km if step_km is not None else _KM_POINT_STEP
    km_max = int(np.ceil(float(cumulative_km[-1])))
    # Sempre ligado e fora do LayerControl (obrigatório — sem toggle).
    fg = folium.FeatureGroup(name="km", show=True, control=False)
    for km in range(0, km_max + 1, step):
        pos = position_at_km(float(km), main_line, cumulative_km)
        if pos is None:
            continue
        lat_m, lon_m = pos
        is_hub = km > 0 and km % _KM_POINT_HUB_EVERY == 0
        is_major = km > 0 and km % _KM_POINT_MAJOR_EVERY == 0
        if is_hub:
            radius, color, fill, fill_op, weight = 5.2, "#0f172a", "#f8fafc", 0.95, 1.6
        elif is_major:
            radius, color, fill, fill_op, weight = 3.8, "#334155", "#e2e8f0", 0.88, 1.2
        else:
            radius, color, fill, fill_op, weight = (
                _KM_POINT_RADIUS,
                _KM_POINT_COLOR,
                _KM_POINT_FILL,
                0.55,
                0.8,
            )
        folium.CircleMarker(
            location=[lat_m, lon_m],
            radius=radius,
            color=color,
            fill=True,
            fillColor=fill,
            fillOpacity=fill_op,
            weight=weight,
            opacity=0.9,
            popup=folium.Popup(f"<b>km {km}</b><br>EFC", max_width=120),
            tooltip=f"km {km}",
        ).add_to(fg)
    fg.add_to(m)


def _station_last_obs_timestamp(last_obs_txt: str | None):
    txt = str(last_obs_txt or "").strip()
    if not txt:
        return None
    try:
        ts = pd.Timestamp(txt)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts
    except Exception:
        return None


def _station_accum_popup_html(
    *,
    title_html: str,
    mm24: float | None,
    mm120: float | None,
    last_obs_txt: str = "",
    extra_html: str = "",
    mm_selected: float | None = None,
    selected_hours: int | None = None,
) -> str:
    """Bloco HTML do popup: janela do menu + 24 h / 120 h de referência."""
    lines = [title_html]
    h_sel = int(selected_hours) if selected_hours is not None else None
    if mm_selected is not None and h_sel and h_sel not in (24, 120):
        lines.append(f"Acumulado últimas {h_sel} h: <b>{float(mm_selected):.1f} mm</b>")
    if mm24 is not None:
        lines.append(f"Acumulado últimas 24 h: <b>{mm24:.1f} mm</b>")
    if mm120 is not None:
        lines.append(f"Acumulado últimas 120 h: <b>{mm120:.1f} mm</b>")
    if last_obs_txt:
        lines.append(f"Último dado: {last_obs_txt}")
    if extra_html:
        lines.append(extra_html)
    return "<br>".join(lines)


def _station_accum_tooltip_suffix(mm24: float | None, mm120: float | None) -> str:
    parts: list[str] = []
    if mm24 is not None:
        parts.append(f"24 h: {mm24:.1f} mm")
    if mm120 is not None:
        parts.append(f"120 h: {mm120:.1f} mm")
    return (" · " + " · ".join(parts)) if parts else ""


# Aliases INMET/CEMADEN (mapa INMET D####) → código BNDMET/canônico no catálogo EFC.
# D0108 Nova Vida (CEMADEN 150553602A) é o mesmo sítio físico que BNDMET D6182.
_STATION_CODE_ALIASES: dict[str, str] = {
    "D0108": "D6182",
}
# Quando ambos existem no catálogo, preferir o código INMET/CEMADEN no marcador.
_STATION_DISPLAY_PREFERRED: dict[str, str] = {
    "D6182": "D0108",
}


def _bndmet_cod_norm(cod: str | None) -> str:
    return _re.sub(r"\.0$", "", str(cod or "").strip())


def _bndmet_metric_lookup(metric_map: dict[str, float] | None, cod: str) -> float:
    """Resolve acumulado / precip por código com ou sem sufixo ``.0``."""
    if not metric_map:
        return 0.0
    cod_s = str(cod).strip()
    cod_norm = _bndmet_cod_norm(cod_s)
    alias = _STATION_CODE_ALIASES.get(cod_norm) or _STATION_CODE_ALIASES.get(cod_s)
    keys = [cod_s, cod_norm]
    if alias:
        keys.extend([alias, _bndmet_cod_norm(alias)])
    # Also accept reverse: canonical asked, alias has data
    for src, dst in _STATION_CODE_ALIASES.items():
        if _bndmet_cod_norm(dst) == cod_norm:
            keys.append(src)
    for k in keys:
        if k in metric_map:
            try:
                return float(metric_map[k])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _bndmet_last_obs_lookup(last_obs: dict[str, str] | None, cod: str) -> str:
    if not last_obs:
        return ""
    cod_s = str(cod).strip()
    cod_norm = _bndmet_cod_norm(cod_s)
    keys = [cod_s, cod_norm]
    alias = _STATION_CODE_ALIASES.get(cod_norm) or _STATION_CODE_ALIASES.get(cod_s)
    if alias:
        keys.extend([alias, _bndmet_cod_norm(alias)])
    for src, dst in _STATION_CODE_ALIASES.items():
        if _bndmet_cod_norm(dst) == cod_norm:
            keys.append(src)
    for k in keys:
        if k in last_obs and last_obs[k]:
            return str(last_obs[k])
    return ""


def _station_alias_canonical(cod: str | None) -> str | None:
    """Canonical group id for known colocated aliases (D0108↔D6182), else None."""
    cod_n = _bndmet_cod_norm(cod)
    if not cod_n:
        return None
    if cod_n in _STATION_CODE_ALIASES:
        return _bndmet_cod_norm(_STATION_CODE_ALIASES[cod_n])
    if cod_n in _STATION_DISPLAY_PREFERRED:
        return cod_n
    for src, dst in _STATION_CODE_ALIASES.items():
        if _bndmet_cod_norm(dst) == cod_n:
            return cod_n
    for src, dst in _STATION_DISPLAY_PREFERRED.items():
        if _bndmet_cod_norm(dst) == cod_n:
            return _bndmet_cod_norm(src)
    return None


def _bndmet_station_map_position_key(row, lat_c: str, lon_c: str, nome_c: str | None = None, cod_c: str | None = None) -> tuple:
    if cod_c:
        try:
            alias_g = _station_alias_canonical(str(row.get(cod_c, "")))
        except Exception:
            alias_g = None
        if alias_g:
            return ("alias", alias_g)
    nome = ""
    if nome_c:
        try:
            nome = str(row.get(nome_c, "")).strip().upper()
        except Exception:
            nome = ""
    try:
        # 3 decimals ≈ 100 m: colocated D0108/D6182 (-49.9045 vs -49.9044) share a marker.
        lat_k = round(float(row[lat_c]), 3)
        lon_k = round(float(row[lon_c]), 3)
    except (TypeError, ValueError):
        lat_k = lon_k = None
    return (nome, lat_k, lon_k)


def _bndmet_expand_last_obs_for_catalog(
    estacoes_df: pd.DataFrame,
    last_obs: dict[str, str] | None,
    stale: set[str] | None,
    *,
    stale_days: int = 3,
) -> tuple[dict[str, str], set[str]]:
    """
    Mesma posição pode ter vários ``codEstacao`` (ex.: 82376 e A255 em Zé Doca).
    Propaga o ``last_obs`` mais recente do grupo para todos os códigos do catálogo.
    """
    expanded = dict(last_obs or {})
    expanded_stale = set(stale or set())
    if estacoes_df is None or estacoes_df.empty or not expanded:
        return expanded, expanded_stale
    lat_c = next((c for c in estacoes_df.columns if "lat" in c.lower()), None)
    lon_c = next((c for c in estacoes_df.columns if "lon" in c.lower()), None)
    cod_c = next((c for c in ["codEstacao", "codigo", "id"] if c in estacoes_df.columns), None)
    if not (lat_c and lon_c and cod_c):
        return expanded, expanded_stale
    nome_c = "nome" if "nome" in estacoes_df.columns else None
    stale_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=int(stale_days))

    groups: dict[tuple, list[str]] = {}
    for _, row in estacoes_df.iterrows():
        cod = str(row.get(cod_c, "")).strip()
        if not cod:
            continue
        groups.setdefault(
            _bndmet_station_map_position_key(row, lat_c, lon_c, nome_c, cod_c),
            [],
        ).append(cod)

    for codes in groups.values():
        best_txt = ""
        best_ts = None
        group_recent = False
        for cod in codes:
            lo = _bndmet_last_obs_lookup(expanded, cod)
            ts = _station_last_obs_timestamp(lo)
            if ts is not None and ts >= stale_cutoff:
                group_recent = True
            if ts is not None and (best_ts is None or ts > best_ts):
                best_ts = ts
                best_txt = lo
        if not best_txt:
            continue
        for cod in codes:
            expanded[cod] = best_txt
            cn = _bndmet_cod_norm(cod)
            if cn != cod:
                expanded[cn] = best_txt
            if group_recent:
                expanded_stale.discard(cod)
                expanded_stale.discard(cn)
    return expanded, expanded_stale


def _dedupe_bndmet_estacoes_for_map(
    estacoes_df: pd.DataFrame,
    station_accum_120h: dict[str, float] | None,
    station_last_obs: dict[str, str] | None,
) -> pd.DataFrame:
    """
    Mesmo nome/coordenadas podem ter vários ``codEstacao`` (ex.: ZE DOCA 82376 vs A255).
    Mantém uma linha por lat/lon, preferindo maior acumulado / obs recente e código INMET/CEMADEN (ex.: D0108 sobre D6182).
    """
    if estacoes_df is None or estacoes_df.empty:
        return estacoes_df
    lat_c = next((c for c in estacoes_df.columns if "lat" in c.lower()), None)
    lon_c = next((c for c in estacoes_df.columns if "lon" in c.lower()), None)
    cod_c = next((c for c in ["codEstacao", "codigo", "id"] if c in estacoes_df.columns), None)
    nome_c = "nome" if "nome" in estacoes_df.columns else None
    if not (lat_c and lon_c and cod_c):
        return estacoes_df

    def _group_key(row) -> tuple:
        # Agrupa por alias canônico (D0108↔D6182) ou por posição (~100 m).
        cod = str(row.get(cod_c, "")).strip()
        alias_g = _station_alias_canonical(cod)
        if alias_g:
            return ("alias", alias_g)
        try:
            lat_k = round(float(row[lat_c]), 3)
            lon_k = round(float(row[lon_c]), 3)
        except (TypeError, ValueError):
            lat_k = lon_k = None
        return ("xy", lat_k, lon_k)

    def _score(row) -> tuple:
        cod = str(row.get(cod_c, "")).strip()
        cod_n = _bndmet_cod_norm(cod)
        acc = _bndmet_metric_lookup(station_accum_120h, cod)
        lo = _bndmet_last_obs_lookup(station_last_obs, cod)
        has_obs = 1 if lo else 0
        # Preferir código INMET/CEMADEN (D0108) sobre espelho BNDMET (D6182) no mesmo ponto.
        prefer = 1 if cod_n in _STATION_CODE_ALIASES or cod_n in _STATION_DISPLAY_PREFERRED.values() else 0
        if _STATION_DISPLAY_PREFERRED.get(cod_n):
            prefer = 0
        return (acc, has_obs, prefer)

    picked_idx: list = []
    for _, grp in estacoes_df.groupby(estacoes_df.apply(_group_key, axis=1), sort=False):
        if len(grp) == 1:
            picked_idx.append(grp.index[0])
            continue
        scores = grp.apply(_score, axis=1)
        picked_idx.append(int(scores.idxmax()))
    return estacoes_df.loc[sorted(picked_idx)].reset_index(drop=True)


def _add_bndmet_station_markers(
    m,
    estacoes_df,
    station_precip: dict | None = None,
    *,
    station_accum_120h: dict[str, float] | None = None,
    station_stale: set[str] | None = None,
    station_last_obs: dict[str, str] | None = None,
    radius: int = 5,
    selected_station_code: str | None = None,
    accum_hours: int = 120,
    station_accum_24h: dict[str, float] | None = None,
    station_accum_120h_popup: dict[str, float] | None = None,
    parent=None,
):
    """Marcadores BNDMET: cor pelo acumulado ``accum_hours``; popup com 24 h e 120 h quando disponível."""
    import folium

    if estacoes_df is None or estacoes_df.empty:
        return
    target = parent if parent is not None else m
    lat_c = next((c for c in estacoes_df.columns if "lat" in c.lower()), None)
    lon_c = next((c for c in estacoes_df.columns if "lon" in c.lower()), None)
    nome_c = "nome" if "nome" in estacoes_df.columns else next((c for c in estacoes_df.columns if "cod" in c.lower()), None)
    cod_c = next((c for c in ["codEstacao", "codigo", "id"] if c in estacoes_df.columns), None)
    if not (lat_c and lon_c):
        return

    use_accum = station_accum_120h is not None
    stale_codes = station_stale or set()
    last_obs = station_last_obs or {}
    selected_norm = _bndmet_cod_norm(selected_station_code)

    if use_accum and last_obs:
        last_obs, stale_codes = _bndmet_expand_last_obs_for_catalog(
            estacoes_df, last_obs, stale_codes
        )

    if use_accum:
        estacoes_df = _dedupe_bndmet_estacoes_for_map(
            estacoes_df, station_accum_120h, last_obs
        )

    colormap = None
    if use_accum:
        pos_vals = [float(v) for v in station_accum_120h.values() if float(v) > 0.05]
        vmax_raw = max(pos_vals) if pos_vals else 0.0
        vmax = max(8.0, min(400.0, vmax_raw * 1.05 + 1e-6)) if vmax_raw > 0 else 8.0
        try:
            setattr(m, "_efc_station_legend_html", _station_accum_legend_html(vmax, accum_hours=int(accum_hours)))
            setattr(m, "_efc_station_colormap_vmax", float(vmax))
        except Exception:
            pass
        try:
            from branca.colormap import LinearColormap

            colormap = LinearColormap(
                colors=["#f8fafc", "#93c5fd", "#2563eb", "#1e1b4b"],
                vmin=0.0,
                vmax=float(vmax),
            )
        except Exception:
            try:
                import matplotlib

                matplotlib.use("Agg")
                import matplotlib.colors as mcolors
                import matplotlib.pyplot as plt

                try:
                    _cmap = plt.colormaps.get_cmap("Blues")
                except Exception:
                    _cmap = plt.cm.Blues
                _norm = mcolors.Normalize(vmin=0.0, vmax=float(vmax))

                def colormap(x: float) -> str:  # type: ignore[no-redef]
                    return mcolors.to_hex(_cmap(_norm(float(x))))

            except Exception:
                colormap = None

    for _, row in estacoes_df.iterrows():
        try:
            lat_v, lon_v = float(row[lat_c]), float(row[lon_c])
            if np.isnan(lat_v) or np.isnan(lon_v):
                continue
            nome = str(row.get(nome_c, row.get(cod_c, ""))) if nome_c else str(row.get(cod_c, ""))
            cod = str(row.get(cod_c, "")).strip()
            cod_norm = _bndmet_cod_norm(cod)
            is_selected = bool(selected_norm) and cod_norm == selected_norm
            if use_accum:
                last_obs_txt = _bndmet_last_obs_lookup(last_obs, cod)
                obs_ts = _station_last_obs_timestamp(last_obs_txt)
                hide_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=_STATION_HIDE_AFTER_DAYS)
                # Keep marker visible even when last obs is older than hide window (gray/stale).
                force_stale_age = bool(obs_ts is not None and obs_ts < hide_cutoff)
                mm_color = _bndmet_metric_lookup(station_accum_120h, cod)
                mm24 = (
                    _bndmet_metric_lookup(station_accum_24h, cod)
                    if station_accum_24h is not None
                    else (mm_color if int(accum_hours) == 24 else None)
                )
                mm120_popup = (
                    _bndmet_metric_lookup(station_accum_120h_popup, cod)
                    if station_accum_120h_popup is not None
                    else (mm_color if int(accum_hours) == 120 else None)
                )
                is_stale = (
                    force_stale_age
                    or (cod in stale_codes or cod_norm in stale_codes)
                    or not last_obs_txt
                    or obs_ts is None
                )
                # Chuva na janela selecionada tem prioridade sobre "stale"
                # (senão o menu Acumulado não muda as cores do mapa).
                if mm_color >= 0.05:
                    hx = colormap(mm_color) if colormap is not None else "#2563eb"
                    border_color, fill_color = hx, hx
                    lo = last_obs_txt
                    body = _station_accum_popup_html(
                        title_html=f"<b>BNDMET {cod}</b><br>{nome}",
                        mm24=mm24,
                        mm120=mm120_popup,
                        last_obs_txt=lo,
                        mm_selected=mm_color,
                        selected_hours=int(accum_hours),
                    )
                    tooltip_text = (
                        (f"BNDMET — {nome}" if nome else f"BNDMET {cod}")
                        + _station_accum_tooltip_suffix(mm24, mm120_popup)
                    )
                    if is_stale:
                        tip_extra = " · série antiga"
                        tooltip_text = f"{tooltip_text}{tip_extra}"
                elif is_stale:
                    border_color, fill_color = _STATION_COLOR_STALE
                    tip_extra = " · sem dados recentes"
                    body = (
                        f"<b>BNDMET {cod}</b><br>{nome}<br>"
                        f"<span style='color:#a8a29e'>Sem dados nos últimos dias (BNDMET)</span>"
                    )
                    tooltip_text = f"BNDMET — {nome}{tip_extra}" if nome else f"BNDMET {cod}{tip_extra}"
                else:
                    border_color, fill_color = _STATION_COLOR_NO_RAIN_120H
                    tip_extra = f" · sem chuva ({int(accum_hours)} h)"
                    lo = last_obs_txt
                    title = f"<b>BNDMET {cod}</b><br>{nome}<br><span style='color:#94a3b8'>Sem chuva registrada (I175)</span>"
                    body = _station_accum_popup_html(
                        title_html=title,
                        mm24=mm24,
                        mm120=mm120_popup,
                        last_obs_txt=lo,
                        mm_selected=mm_color,
                        selected_hours=int(accum_hours),
                    )
                    tooltip_text = (
                        (f"BNDMET — {nome}" if nome else f"BNDMET {cod}")
                        + tip_extra
                        + _station_accum_tooltip_suffix(mm24, mm120_popup)
                    )
                has_rain = mm_color >= 0.05
                _r_st = radius if (not has_rain) else radius + (1 if mm_color >= 40 else 0)
                _op_st = 0.0 if (not has_rain) else 0.88
                if not has_rain:
                    fill_color = _TRANSPARENT_FILL
                    _r_st = max(2, radius - 2)
                    _w_st = 1.0
                else:
                    _w_st = 1.7
                if is_selected:
                    border_color, fill_color = "#ef4444", "#ef4444"
                    _r_st = radius + 3
                    _op_st = 0.95
                    _w_st = 1.8
                    body += "<br><b>Estação selecionada</b>"
                    tooltip_text = f"BNDMET — {nome} · estação selecionada" if nome else f"BNDMET {cod}"
                folium.CircleMarker(
                    location=[lat_v, lon_v],
                    radius=_r_st,
                    color=border_color,
                    fill=True,
                    fillColor=fill_color,
                    fillOpacity=_op_st,
                    weight=_w_st,
                    popup=folium.Popup(body, max_width=220),
                    tooltip=tooltip_text,
                ).add_to(target)
                continue

            sp = station_precip or {}
            precip_mm = float(sp.get(cod, 0.0))
            rained = precip_mm > 0
            border_color, fill_color = _STATION_COLOR_RAIN if rained else _STATION_COLOR_DRY
            mm24 = precip_mm if sp else _bndmet_metric_lookup(station_accum_24h, cod)
            mm120 = _bndmet_metric_lookup(station_accum_120h_popup, cod)
            popup = _station_accum_popup_html(
                title_html=f"<b>BNDMET {cod}</b><br>{nome}" if nome else f"<b>BNDMET {cod}</b>",
                mm24=mm24,
                mm120=mm120,
            )
            tooltip_text = (
                (f"BNDMET — {nome}" if nome else f"BNDMET {cod}")
                + _station_accum_tooltip_suffix(mm24, mm120)
            )
            _r = radius + (1 if rained else 0)
            _w = 1.8 if rained else 1.5
            _op = 0.85
            if is_selected:
                border_color, fill_color = "#ef4444", "#ef4444"
                popup += "<br><b>Estação selecionada</b>"
                tooltip_text = f"BNDMET — {nome} · estação selecionada" if nome else f"BNDMET {cod}"
                _r = radius + 3
                _w = 2.8
                _op = 0.95
            folium.CircleMarker(
                location=[lat_v, lon_v],
                radius=_r,
                color=border_color,
                fill=True,
                fillColor=fill_color,
                fillOpacity=_op,
                weight=_w,
                popup=popup,
                tooltip=tooltip_text,
            ).add_to(target)
        except (ValueError, TypeError):
            continue


def _add_ana_station_markers(
    m,
    estacoes_df,
    *,
    station_accum_120h: dict[str, float] | None = None,
    station_stale: set[str] | None = None,
    station_last_obs: dict[str, str] | None = None,
    radius: int = 4,
    accum_hours: int = 120,
    station_accum_24h: dict[str, float] | None = None,
    station_accum_120h_popup: dict[str, float] | None = None,
    parent=None,
):
    """Marcadores ANA com a mesma semântica visual do BNDMET."""
    import folium

    if estacoes_df is None or estacoes_df.empty:
        return
    target = parent if parent is not None else m
    lat_c = next((c for c in estacoes_df.columns if "lat" in c.lower()), None)
    lon_c = next((c for c in estacoes_df.columns if "lon" in c.lower()), None)
    cod_c = next((c for c in ["codEstacao", "codigo", "id"] if c in estacoes_df.columns), None)
    nome_c = "nome" if "nome" in estacoes_df.columns else cod_c
    if not (lat_c and lon_c and cod_c):
        return
    use_accum = station_accum_120h is not None
    stale_codes = station_stale or set()
    last_obs = station_last_obs or {}

    colormap = None
    if use_accum:
        pos_vals = [float(v) for v in station_accum_120h.values() if float(v) > 0.05]
        vmax_raw = max(pos_vals) if pos_vals else 0.0
        shared_vmax = getattr(m, "_efc_station_colormap_vmax", None)
        vmax = float(shared_vmax) if shared_vmax is not None else (max(8.0, min(400.0, vmax_raw * 1.05 + 1e-6)) if vmax_raw > 0 else 8.0)
        try:
            if not getattr(m, "_efc_station_legend_html", ""):
                setattr(m, "_efc_station_legend_html", _station_accum_legend_html(vmax, accum_hours=int(accum_hours)))
        except Exception:
            pass
        try:
            from branca.colormap import LinearColormap

            colormap = LinearColormap(
                colors=["#f8fafc", "#93c5fd", "#2563eb", "#1e1b4b"],
                vmin=0.0,
                vmax=float(vmax),
            )
        except Exception:
            try:
                import matplotlib

                matplotlib.use("Agg")
                import matplotlib.colors as mcolors
                import matplotlib.pyplot as plt

                try:
                    _cmap = plt.colormaps.get_cmap("Blues")
                except Exception:
                    _cmap = plt.cm.Blues
                _norm = mcolors.Normalize(vmin=0.0, vmax=float(vmax))

                def colormap(x: float) -> str:  # type: ignore[no-redef]
                    return mcolors.to_hex(_cmap(_norm(float(x))))

            except Exception:
                colormap = None

    for _, row in estacoes_df.iterrows():
        try:
            lat_v, lon_v = float(row[lat_c]), float(row[lon_c])
        except (TypeError, ValueError):
            continue
        if np.isnan(lat_v) or np.isnan(lon_v):
            continue
        cod = str(row.get(cod_c, "")).strip()
        nome = str(row.get(nome_c, cod)).strip()
        mun = str(row.get("municipio_uf", "")).strip()
        origem = str(row.get("origem", "")).strip()
        status = str(row.get("statusEstacao", "")).strip()
        details = [f"<b>ANA {cod}</b>", nome]
        if mun:
            details.append(mun)
        meta = []
        if origem:
            meta.append(f"Origem: {origem}")
        if status:
            meta.append(f"Status: {status}")
        if use_accum:
            last_obs_txt = last_obs.get(cod, "")
            obs_ts = _station_last_obs_timestamp(last_obs_txt)
            hide_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=_STATION_HIDE_AFTER_DAYS)
            # Keep marker visible even when last obs is older than hide window (gray/stale).
            force_stale_age = bool(obs_ts is not None and obs_ts < hide_cutoff)
            mm_color = float(station_accum_120h.get(cod, 0.0))
            mm24 = (
                float(station_accum_24h.get(cod, 0.0))
                if station_accum_24h is not None
                else (mm_color if int(accum_hours) == 24 else None)
            )
            mm120_popup = (
                float(station_accum_120h_popup.get(cod, 0.0))
                if station_accum_120h_popup is not None
                else (mm_color if int(accum_hours) == 120 else None)
            )
            is_stale = force_stale_age or (cod in stale_codes) or (cod not in last_obs) or obs_ts is None
            lo = last_obs.get(cod, "")
            if mm_color >= 0.05:
                hx = colormap(mm_color) if colormap is not None else "#2563eb"
                border_color, fill_color = hx, hx
                body = _station_accum_popup_html(
                    title_html=f"<b>ANA {cod}</b><br>{nome}",
                    mm24=mm24,
                    mm120=mm120_popup,
                    last_obs_txt=lo,
                    mm_selected=mm_color,
                    selected_hours=int(accum_hours),
                )
                tooltip_text = (
                    (f"ANA — {nome}" if nome else f"ANA {cod}")
                    + _station_accum_tooltip_suffix(mm24, mm120_popup)
                )
            elif is_stale:
                border_color, fill_color = _STATION_COLOR_STALE
                body = (
                    f"<b>ANA {cod}</b><br>{nome}<br>"
                    f"<span style='color:#a8a29e'>Sem dados nos últimos dias</span>"
                )
                tooltip_text = f"ANA — {nome} · sem dados recentes" if nome else f"ANA {cod}"
            else:
                border_color, fill_color = _STATION_COLOR_NO_RAIN_120H
                title = (
                    f"<b>ANA {cod}</b><br>{nome}<br>"
                    f"<span style='color:#94a3b8'>Sem chuva registrada</span>"
                )
                body = _station_accum_popup_html(
                    title_html=title,
                    mm24=mm24,
                    mm120=mm120_popup,
                    last_obs_txt=lo,
                    mm_selected=mm_color,
                    selected_hours=int(accum_hours),
                )
                tooltip_text = (
                    (f"ANA — {nome} · sem chuva" if nome else f"ANA {cod}")
                    + _station_accum_tooltip_suffix(mm24, mm120_popup)
                )
            if meta:
                body += "<br>" + "<br>".join(meta)
            has_rain = mm_color >= 0.05
            _r = radius if (not has_rain) else radius + (1 if mm_color >= 40 else 0)
            _op = 0.0 if (not has_rain) else 0.88
            if not has_rain:
                fill_color = _TRANSPARENT_FILL
                _r = max(2, radius - 2)
                _w = 1.0
            else:
                _w = 1.6
        else:
            body = "<br>".join([d for d in details if d])
            if meta:
                body += "<br>" + "<br>".join(meta)
            border_color, fill_color = _ANA_STATION_COLOR
            tooltip_text = f"ANA — {nome or cod}"
            _r = radius
            _op = 0.68
            _w = 1.6
        folium.CircleMarker(
            location=[lat_v, lon_v],
            radius=_r,
            color=border_color,
            fill=True,
            fillColor=fill_color,
            fillOpacity=_op,
            weight=_w,
            popup=folium.Popup(body, max_width=240),
            tooltip=tooltip_text,
        ).add_to(target)


# Cor para pontos "quase ultrapassando" 120h (200–250 mm) no mapa
COLOR_ALMOST_120H = "#fbbf24"  # âmbar suave

def build_folium_railway_map(
    main_line,
    all_lines,
    cumulative_km,
    lat_arr,
    lon_arr,
    values_2d,
    threshold_mm,
    color_hex,
    title,
    date_label,
    color_single_hex=None,
    estacoes_df=None,
    ana_estacoes_df=None,
    kmz_path: Path = None,
    points_above_override=None,
    points_almost_120h_override=None,
    add_suscetibilidade=True,
    show_suscetibilidade: bool = False,
    markers_100=None,
    show_km_zoom_markers: bool = True,
    show_clickable_km_points: bool = True,
    show_layer_control: bool = True,
    draw_railway_polyline: bool = True,
    railway_line_color: str | None = None,
    railway_line_weight: float | None = None,
    fit_main_line_bounds: bool = False,
    km_point_step_override: int | None = None,
    station_precip: dict | None = None,
    station_accum_120h: dict[str, float] | None = None,
    station_stale: set[str] | None = None,
    station_last_obs: dict[str, str] | None = None,
    ana_station_accum_120h: dict[str, float] | None = None,
    ana_station_stale: set[str] | None = None,
    ana_station_last_obs: dict[str, str] | None = None,
    station_accum_hours: int = 120,
    station_accum_24h: dict[str, float] | None = None,
    station_accum_120h_popup: dict[str, float] | None = None,
    ana_station_accum_24h: dict[str, float] | None = None,
    ana_station_accum_120h_popup: dict[str, float] | None = None,
):
    """
    Constrói mapa Folium: base satélite + (opcional) camada suscetibilidade (TIFF) + ferrovia + pontos acima do limiar.
    points_above_override: se fornecido, lista de (lat, lon, value) para plotar em vermelho (24h) ou cor do limiar (modo BQ).
    points_almost_120h_override: se fornecido, lista de (lat, lon, value) para plotar em amarelo (quase ultrapassando 120h).
    add_suscetibilidade: se False, não carrega o GeoTIFF (economia de memória no modo leve).
    show_suscetibilidade: se True, overlay Suscetibilidade inicia ligado (LayerControl).
    markers_100: dict {"100": [...], "50": [...], "10": [...]} ou lista flat (retrocompat.).
    show_km_zoom_markers: se False, não adiciona camadas «Km (100)/(50)/(10)» ao Folium.
    show_clickable_km_points: se False, não adiciona bolinhas clicáveis ao longo de toda a ferrovia.
    show_layer_control: se False, não adiciona o controlo Folium de camadas (Padrão/Satélite/Suscet.).
    station_precip: legado — precipitação 24 h por estação (azul/verde).
    station_accum_120h / station_stale / station_last_obs: estilo BNDMET por acumulado nas últimas ``station_accum_hours`` + cinza se sem dados recentes.
    Retorna objeto folium.Map ou None se folium não disponível.
    """
    try:
        import os
        import folium
        from folium.plugins import MeasureControl
    except ImportError:
        return None
    path = kmz_path if (kmz_path is not None and kmz_path.exists()) else get_railway_geometry_path()
    if path is None:
        path = KMZ_PATH
    fit_pts = _railway_bounds_points(main_line, _filter_efc_railway_lines(list(all_lines or [])), prefer_all_lines=fit_main_line_bounds)
    map_max_bounds = None
    if fit_main_line_bounds:
        pad = float(os.environ.get("EFC_MAP_FIT_PADDING_DEG", "0.06"))
        map_max_bounds = [
            [LAT_MIN_EFC - pad, LON_MIN_EFC - pad],
            [LAT_MAX_EFC + pad, LON_MAX_EFC + pad],
        ]
    if fit_pts:
        fit_lats = [p[0] for p in fit_pts]
        fit_lons = [p[1] for p in fit_pts]
        center_lat = (min(fit_lats) + max(fit_lats)) / 2
        center_lon = (min(fit_lons) + max(fit_lons)) / 2
        span = max(max(fit_lats) - min(fit_lats), max(fit_lons) - min(fit_lons))
        zoom_start = 10 if span < 0.35 else 9 if span < 0.75 else 8 if span < 1.5 else 7
    else:
        center_lat = (LAT_MIN_EFC + LAT_MAX_EFC) / 2
        center_lon = (LON_MIN_EFC + LON_MAX_EFC) / 2
        zoom_start = 6
    # Base: imagem ~2 m (Esri). Controlo compacto no parent (sem OSM / sem toggle de km).
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom_start,
        tiles=None,
        control_scale=True,
        max_zoom=EFC_MAP_MAX_ZOOM,
        max_bounds=map_max_bounds,
        min_zoom=5 if map_max_bounds else None,
    )
    _add_folium_basemap_layers(m, satellite_visible=True)
    # Camada de suscetibilidade — padrão: faixa colorida sobre o eixo ferroviário.
    # Sempre registra o overlay no LayerControl (mesmo se o raster falhar) para o
    # checkbox "Suscetibilidade" não sumir após trocas de data / rebuild.
    # show_suscetibilidade: estado inicial (query ?susc=1 / preferência do browser).
    # Com show=False: só um FeatureGroup vazio no LayerControl (mapa leve).
    # O JS do monitor recarrega com ?susc=1 quando o usuário liga a camada.
    if add_suscetibilidade:
        mode = _suscetibilidade_map_mode()
        susc_ok = False
        susc_show = bool(show_suscetibilidade)
        if susc_show:
            if mode in ("rail", "line", "corridor", "track"):
                susc_ok = bool(
                    _add_suscetibilidade_railway_layer(
                        m, main_line, cumulative_km, show=True
                    )
                )
            else:
                susc_max_px = int(os.environ.get("SUSCETIBILIDADE_MAP_MAX_PX", "1024")) or 1024
                susc_ok = bool(
                    _add_suscetibilidade_raster_layer(
                        m, main_line, max_pixels=susc_max_px, show=True
                    )
                )
        if not susc_ok:
            # Mantém overlay no mapa; controlo compacto no parent (sem LayerControl Folium).
            folium.FeatureGroup(name="Suscetibilidade", show=susc_show, control=False).add_to(m)
    if all_lines:
        all_lines = _filter_efc_railway_lines(list(all_lines))
        # Reduzir pontos/linhas no desenho para ficar abaixo do limite Streamlit (200 MB)
        max_pts_display = 200
        max_lines_display = 25
        lines_to_draw = all_lines if len(all_lines) <= max_lines_display else sorted(all_lines, key=_line_length_km, reverse=True)[:max_lines_display]
        display_lines = []
        all_lats, all_lons = [], []
        for line in lines_to_draw:
            if len(line) > max_pts_display:
                step = max(1, len(line) // max_pts_display)
                line = line[::step]
            display_lines.append(line)
            for p in line:
                all_lats.append(p[0])
                all_lons.append(p[1])
        if not fit_main_line_bounds and all_lats and all_lons:
            m.fit_bounds([[min(all_lats) - 0.5, min(all_lons) - 0.5], [max(all_lats) + 0.5, max(all_lons) + 0.5]])
        line_color = railway_line_color or _RAILWAY_LINE_COLOR
        line_weight = float(railway_line_weight) if railway_line_weight is not None else _RAILWAY_LINE_WEIGHT
        if draw_railway_polyline:
            _draw_railway_polyline(
                m,
                display_lines,
                line_color=line_color,
                line_weight=line_weight,
                popup="Linha EFC",
            )
        cum_km = cumulative_km
        mk_inner = None
        needs_geom_reload = (show_clickable_km_points and cum_km is None) or (show_km_zoom_markers and markers_100 is None)
        if needs_geom_reload:
            _main, cum_loaded, mk_loaded, _ = get_railway_data(path)
            if cum_km is None:
                cum_km = cum_loaded
            mk_inner = mk_loaded
        if show_clickable_km_points and main_line is not None and cum_km is not None:
            _add_railway_km_points(m, main_line, cum_km, step_km=km_point_step_override)
        # Markers de quilometragem — zoom progressivo
        mk = markers_100 if markers_100 is not None else mk_inner
        if mk is None:
            mk = mk_inner
        if show_km_zoom_markers:
            if isinstance(mk, dict):
                mk_dict = mk
            elif isinstance(mk, list) and len(mk) > 0:
                mk_dict = {"100": mk}
            else:
                mk_dict = {}
            _add_zoom_markers(m, mk_dict)
    else:
        if not fit_main_line_bounds:
            m.fit_bounds([[LAT_MIN_EFC, LON_MIN_EFC], [LAT_MAX_EFC, LON_MAX_EFC]])
        if draw_railway_polyline and main_line and len(main_line) >= 2:
            line_color = railway_line_color or _RAILWAY_LINE_COLOR
            line_weight = float(railway_line_weight) if railway_line_weight is not None else _RAILWAY_LINE_WEIGHT
            _draw_railway_polyline(
                m,
                [main_line],
                line_color=line_color,
                line_weight=line_weight,
                popup="Linha EFC",
            )
    if points_above_override is not None:
        points_above = [(float(la), float(lo), float(v)) for la, lo, v in points_above_override]
    else:
        z = np.nan_to_num(values_2d, nan=0)
        nlat, nlon = z.shape[0], z.shape[1]
        points_above = [(float(lat_arr[i] if lat_arr.ndim == 1 else lat_arr[i, j]), float(lon_arr[j] if lon_arr.ndim == 1 else lon_arr[i, j]), float(z[i, j])) for i in range(nlat) for j in range(nlon) if float(z[i, j]) > threshold_mm]
    use_single = color_single_hex is not None and len(points_above) == 1
    point_color = color_single_hex if use_single else color_hex
    for lat_c, lon_c, v in points_above:
        folium.CircleMarker(
            location=[lat_c, lon_c],
            radius=6 if use_single else 4,
            color=point_color,
            fill=True,
            fillColor=point_color,
            fillOpacity=0.74,
            weight=1.5 if use_single else 1.1,
            popup=f"{title}<br>{date_label}<br>{v:.1f} mm (limiar {threshold_mm} mm)",
        ).add_to(m)
    # Pontos "quase ultrapassando" 120h (200–250 mm) em amarelo
    if points_almost_120h_override:
        for lat_c, lon_c, v in points_almost_120h_override:
            la, lo, val = float(lat_c), float(lon_c), float(v)
            folium.CircleMarker(
                location=[la, lo],
                radius=4,
                color=COLOR_ALMOST_120H,
                fill=True,
                fillColor=COLOR_ALMOST_120H,
                fillOpacity=0.68,
                weight=1.1,
                popup=f"Próximo ao limiar 120h<br>{date_label}<br>{val:.1f} mm (ref. 250 mm)",
            ).add_to(m)
    fg_stations = folium.FeatureGroup(name="Estações", show=True, control=False)
    fg_stations.add_to(m)
    _add_bndmet_station_markers(
        m,
        estacoes_df,
        station_precip,
        station_accum_120h=station_accum_120h,
        station_stale=station_stale,
        station_last_obs=station_last_obs,
        accum_hours=int(station_accum_hours),
        station_accum_24h=station_accum_24h,
        station_accum_120h_popup=station_accum_120h_popup,
        parent=fg_stations,
    )
    _add_ana_station_markers(
        m,
        ana_estacoes_df,
        station_accum_120h=ana_station_accum_120h,
        station_stale=ana_station_stale,
        station_last_obs=ana_station_last_obs,
        accum_hours=int(station_accum_hours),
        station_accum_24h=ana_station_accum_24h,
        station_accum_120h_popup=ana_station_accum_120h_popup,
        parent=fg_stations,
    )
    try:
        from monitor_web.services.zentra_sensors import bind_stations_feature_group

        bind_stations_feature_group(m, fg_stations)
    except Exception:
        pass
    try:
        MeasureControl(primary_length_unit="kilometers", secondary_length_unit="miles", primary_area_unit="sq kilometers", secondary_area_unit="sq miles").add_to(m)
    except Exception:
        pass
    if show_layer_control:
        # topleft: parent Stitch UI puts decorative zoom on topright over the iframe,
        # which used to intercept clicks on Suscetibilidade / Satélite.
        folium.LayerControl(position="topleft", collapsed=False).add_to(m)
    _apply_railway_fit_bounds(m, main_line, all_lines, fit_main_line_bounds=fit_main_line_bounds)
    return m


def build_folium_ensemble_pr_snapshot_map(
    main_line,
    all_lines,
    cumulative_km,
    *,
    date_label: str,
    circles: list[dict],
    kmz_path: Path | None = None,
):
    """
    Mapa Folium (padrão + satélite): ferrovia + círculos proporcionais à mediana de PR (conjunto)
    com cor/saturação já definidas pelo chamador (hex + raio). ``circles``: lista de dicts com
    lat, lon, radius, color, fillColor, fillOpacity, weight, popup (HTML ou texto).
    """
    try:
        import folium
        from folium.plugins import MeasureControl
    except ImportError:
        return None
    path = kmz_path if (kmz_path is not None and kmz_path.exists()) else get_railway_geometry_path()
    center_lat = (LAT_MIN_EFC + LAT_MAX_EFC) / 2
    center_lon = (LON_MIN_EFC + LON_MAX_EFC) / 2
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=6,
        tiles=None,
        control_scale=True,
        max_zoom=EFC_MAP_MAX_ZOOM,
    )
    _add_folium_basemap_layers(m)
    fg = folium.FeatureGroup(name=f"Conjunto PR · {date_label}", show=True)
    if all_lines:
        max_pts_display = 200
        max_lines_display = 25
        lines_to_draw = (
            all_lines if len(all_lines) <= max_lines_display else sorted(all_lines, key=_line_length_km, reverse=True)[:max_lines_display]
        )
        display_lines = []
        all_lats, all_lons = [], []
        for line in lines_to_draw:
            if len(line) > max_pts_display:
                step = max(1, len(line) // max_pts_display)
                line = line[::step]
            display_lines.append(line)
            for p in line:
                all_lats.append(p[0])
                all_lons.append(p[1])
        if all_lats and all_lons:
            pad = 0.5
            m.fit_bounds([[min(all_lats) - pad, min(all_lons) - pad], [max(all_lats) + pad, max(all_lons) + pad]])
        for line in display_lines:
            if len(line) >= 2:
                _draw_railway_polyline(m, [line], popup="Linha EFC")
    else:
        m.fit_bounds([[LAT_MIN_EFC, LON_MIN_EFC], [LAT_MAX_EFC, LON_MAX_EFC]])
    for c in circles:
        try:
            la = float(c["lat"])
            lo = float(c["lon"])
            r = int(max(3, min(28, int(c.get("radius", 8)))))
            col = str(c.get("color", "#334155"))
            fill = str(c.get("fillColor", col))
            op = float(c.get("fillOpacity", 0.72))
            w = float(c.get("weight", 1.2))
            pop = str(c.get("popup", ""))
            folium.CircleMarker(
                location=[la, lo],
                radius=r,
                color=col,
                fill=True,
                fillColor=fill,
                fillOpacity=op,
                weight=w,
                popup=folium.Popup(pop, max_width=280),
                tooltip=c.get("tooltip"),
            ).add_to(fg)
        except Exception:
            continue
    fg.add_to(m)
    try:
        MeasureControl(primary_length_unit="kilometers").add_to(m)
    except Exception:
        pass
    folium.LayerControl(collapsed=False).add_to(m)
    return m


def build_folium_map_focused_km(
    km_float: float,
    main_line,
    cumulative_km,
    all_lines=None,
    estacoes_df=None,
    ana_estacoes_df=None,
    kmz_path: Path = None,
    markers_dict=None,
    station_precip: dict | None = None,
    station_accum_120h: dict[str, float] | None = None,
    station_stale: set[str] | None = None,
    station_last_obs: dict[str, str] | None = None,
    ana_station_accum_120h: dict[str, float] | None = None,
    ana_station_stale: set[str] | None = None,
    ana_station_last_obs: dict[str, str] | None = None,
    selected_station_code: str | None = None,
    station_accum_hours: int = 120,
):
    """
    Mapa Folium focado no quilômetro: ferrovia + marcador no km, bounds em janela ao redor do ponto.
    station_precip: legado 24 h; preferir station_accum_120h + station_stale + station_last_obs.
    """
    try:
        import folium
    except ImportError:
        return None
    pos = position_at_km(float(km_float), main_line, cumulative_km)
    if pos is None:
        center_lat, center_lon = (LAT_MIN_EFC + LAT_MAX_EFC) / 2, (LON_MIN_EFC + LON_MAX_EFC) / 2
    else:
        center_lat, center_lon = pos
    buf = 0.25
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=10,
        tiles=None,
        control_scale=True,
        max_zoom=EFC_MAP_MAX_ZOOM,
    )
    _add_folium_basemap_layers(m, satellite_visible=True)
    if main_line is not None and cumulative_km is not None:
        _add_railway_km_points(m, main_line, cumulative_km, step_km=1)
    if pos is not None:
        folium.Marker(
            [center_lat, center_lon],
            icon=folium.DivIcon(html=f'<div style="font-size:13px;font-weight:bold;background:#c0392b;border:2px solid #922b21;padding:4px 8px;border-radius:5px;color:#fff;box-shadow:0 2px 6px rgba(0,0,0,.4);white-space:nowrap;text-align:center;">{int(km_float)} km</div>'),
            popup=f"Trecho km {int(km_float)}",
        ).add_to(m)
    lat_min_b, lat_max_b = center_lat - buf, center_lat + buf
    lon_min_b, lon_max_b = center_lon - buf, center_lon + buf
    if selected_station_code and estacoes_df is not None and not estacoes_df.empty:
        lat_c = next((c for c in estacoes_df.columns if "lat" in c.lower()), None)
        lon_c = next((c for c in estacoes_df.columns if "lon" in c.lower()), None)
        cod_c = next((c for c in ["codEstacao", "codigo", "id"] if c in estacoes_df.columns), None)
        if lat_c and lon_c and cod_c:
            sel_norm = _re.sub(r"\.0$", "", str(selected_station_code).strip())
            for _, row in estacoes_df.iterrows():
                cod = _re.sub(r"\.0$", "", str(row.get(cod_c, "")).strip())
                if cod != sel_norm:
                    continue
                try:
                    st_lat = float(row[lat_c])
                    st_lon = float(row[lon_c])
                except (TypeError, ValueError):
                    break
                lat_min_b = min(lat_min_b, st_lat - 0.08)
                lat_max_b = max(lat_max_b, st_lat + 0.08)
                lon_min_b = min(lon_min_b, st_lon - 0.08)
                lon_max_b = max(lon_max_b, st_lon + 0.08)
                break
    m.fit_bounds(
        [[lat_min_b, lon_min_b], [lat_max_b, lon_max_b]],
        max_zoom=14,
    )
    _add_bndmet_station_markers(
        m,
        estacoes_df,
        station_precip,
        station_accum_120h=station_accum_120h,
        station_stale=station_stale,
        station_last_obs=station_last_obs,
        radius=6,
        selected_station_code=selected_station_code,
        accum_hours=int(station_accum_hours),
    )
    _add_ana_station_markers(
        m,
        ana_estacoes_df,
        station_accum_120h=ana_station_accum_120h,
        station_stale=ana_station_stale,
        station_last_obs=ana_station_last_obs,
        radius=5,
        accum_hours=int(station_accum_hours),
    )
    return m


def build_folium_estacoes_only(
    all_lines,
    estacoes_df,
    ana_estacoes_df=None,
    kmz_path: Path = None,
    station_precip: dict | None = None,
    station_accum_120h: dict[str, float] | None = None,
    station_stale: set[str] | None = None,
    station_last_obs: dict[str, str] | None = None,
    ana_station_accum_120h: dict[str, float] | None = None,
    ana_station_stale: set[str] | None = None,
    ana_station_last_obs: dict[str, str] | None = None,
    station_accum_hours: int = 120,
):
    """Mapa Folium apenas com ferrovia e pontos das estações BNDMET."""
    try:
        import folium
    except ImportError:
        return None
    path = kmz_path if (kmz_path is not None and kmz_path.exists()) else get_railway_geometry_path()
    center_lat = (LAT_MIN_EFC + LAT_MAX_EFC) / 2
    center_lon = (LON_MIN_EFC + LON_MAX_EFC) / 2
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=6,
        tiles=None,
        control_scale=True,
        max_zoom=EFC_MAP_MAX_ZOOM,
    )
    _add_folium_basemap_layers(m)
    if all_lines:
        all_lats, all_lons = [], []
        for line in all_lines:
            for p in line:
                all_lats.append(p[0])
                all_lons.append(p[1])
        if all_lats and all_lons:
            m.fit_bounds([[min(all_lats) - 0.5, min(all_lons) - 0.5], [max(all_lats) + 0.5, max(all_lons) + 0.5]])
        if path and path.exists():
            main_line, cum_km, _, _ = get_railway_data(path)
            if main_line is not None and cum_km is not None:
                _add_railway_km_points(m, main_line, cum_km, step_km=1)
    _add_bndmet_station_markers(
        m,
        estacoes_df,
        station_precip,
        station_accum_120h=station_accum_120h,
        station_stale=station_stale,
        station_last_obs=station_last_obs,
        accum_hours=int(station_accum_hours),
    )
    _add_ana_station_markers(
        m,
        ana_estacoes_df,
        station_accum_120h=ana_station_accum_120h,
        station_stale=ana_station_stale,
        station_last_obs=ana_station_last_obs,
        accum_hours=int(station_accum_hours),
    )
    if not all_lines:
        lat_series = []
        lon_series = []
        for df_st in (estacoes_df, ana_estacoes_df):
            if df_st is None or df_st.empty:
                continue
            lat_c = next((c for c in df_st.columns if "lat" in c.lower()), None)
            lon_c = next((c for c in df_st.columns if "lon" in c.lower()), None)
            if not (lat_c and lon_c):
                continue
            latv = pd.to_numeric(df_st[lat_c], errors="coerce").dropna()
            lonv = pd.to_numeric(df_st[lon_c], errors="coerce").dropna()
            if len(latv) and len(lonv):
                lat_series.append(latv)
                lon_series.append(lonv)
        if lat_series and lon_series:
            lat_all = pd.concat(lat_series, ignore_index=True)
            lon_all = pd.concat(lon_series, ignore_index=True)
            buf = 0.35
            m.fit_bounds(
                [
                    [float(lat_all.min()) - buf, float(lon_all.min()) - buf],
                    [float(lat_all.max()) + buf, float(lon_all.max()) + buf],
                ]
            )
    return m
