#!/usr/bin/env python3
"""EFC map — polished aesthetics (same analytical content)."""
from __future__ import annotations

import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import folium
from folium import FeatureGroup, plugins
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd

CORRIDOR = Path("/tmp/ana_efc_corridor.json")
OUT_HTML = Path("/workspace/EFC_pontos_nivel_mapa.html")
OUT_PNG = Path("/workspace/EFC_pontos_nivel_mapa.png")
OUT_XLSX = Path("/workspace/EFC_nivel_pontos_resumo.xlsx")
LEGACY_HTML = Path("/workspace/Km661_Ponte_Sumauma_mapa_estacoes.html")
LEGACY_PNG = Path("/workspace/Km661_Ponte_Sumauma_mapa_estacoes.png")

# Palette — restrained, map-ops friendly
EWS_FILL = "#5c3d6e"
EWS_EDGE = "#3d2849"
ANA_FILL = "#1f6f8b"
ANA_EDGE = "#134e63"
ANA_STALE = "#6a9bb0"
ANA_CAT = "#8b95a1"
EFC_LINE = "#243447"
INK = "#1c2430"
MUTED = "#5a6570"
CARD = "#f7f4ef"
CARD_BORDER = "#d9d2c5"

OPS = [
    {
        "id": "km85",
        "nome": "Km 85 (Abrigo EWS)",
        "lat": -3.298651,
        "lon": -44.465063,
        "fonte": "EWS",
        "ews": "EWS6987",
        "km_efc": 85,
        "xlsx": "EWS6987 - Abrigo km 085.xlsx; BS08687S - Abrigo km 085.xlsx",
    },
    {
        "id": "pindare",
        "nome": "Sensor Rio Pindaré (Km 374)",
        "lat": -4.253738,
        "lon": -46.531145,
        "fonte": "EWS / sensor EFC",
        "ews": None,
        "km_efc": 374,
        "xlsx": "(sem xlsx EWS dedicado no catálogo)",
    },
    {
        "id": "ponte763",
        "nome": "Ponte EFC Km 763+800",
        "lat": -5.571403,
        "lon": -49.241924,
        "fonte": "EWS / ponte EFC",
        "ews": "EWS6970",
        "km_efc": 763.8,
        "xlsx": "EWS6970 - Abrigo km 761.xlsx (EWS mais próximo)",
    },
    {
        "id": "km661_eixo",
        "nome": "Km 661 (eixo)",
        "lat": -5.149214340420318,
        "lon": -48.37704830878583,
        "fonte": "BQ railway_km_661 (quilometragem no eixo)",
        "ews": "EWS6971",
        "km_efc": 661,
        "xlsx": "EWS6971 - Abrigo km 663.xlsx (EWS mais próximo)",
    },
    {
        # Vão OSM da EFC (~km 660,8), ~170 m a leste do eixo km 661.
        # Satélite: corredor ripário (igarapé sob dossel); OSM não nomeia o curso d'água.
        # Rio mapeado mais próximo na ferrovia: stream OSM em ~km 665 (~3,9 km a oeste).
        "id": "sumauma_vao",
        "nome": "Ponte Sumaúma (vão)",
        "lat": -5.149460125,
        "lon": -48.375528475,
        "fonte": "OSM bridge EFC ways 309747856+1069345405 (mid dual-track)",
        "ews": "EWS6971",
        "km_efc": 660.83,
        "xlsx": "EWS6971 - Abrigo km 663.xlsx (EWS mais próximo)",
    },
]


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def nearest_ana(ops, ana_stations):
    rows = []
    for p in ops:
        best = min(
            ana_stations,
            key=lambda s: haversine(p["lat"], p["lon"], s["lat"], s["lon"]),
        )
        bestd = haversine(p["lat"], p["lon"], best["lat"], best["lon"])
        rows.append(
            {
                "ponto_ews": p["nome"],
                "fonte_ponto": p["fonte"],
                "ews_code": p.get("ews") or "—",
                "lat": p["lat"],
                "lon": p["lon"],
                "ana_mais_proxima": f"ANA · {best['nome']}",
                "ana_codigo": best["code"],
                "dist_km": round(bestd, 1),
                "ana_ultimo_nivel_m": best.get("last_nivel_m"),
                "ana_ultima_data": best.get("max_date"),
                "ana_tem_nivel_bq": best.get("has_nivel_bq"),
            }
        )
    return rows


def _popup_html(title: str, badge: str, badge_bg: str, rows: list[tuple[str, str]]) -> str:
    meta = "".join(
        f"<div class='meta-row'><span class='k'>{k}</span><span class='v'>{v}</span></div>"
        for k, v in rows
    )
    return f"""
    <div class="efc-popup">
      <div class="efc-title">{title}</div>
      <div class="efc-badge" style="background:{badge_bg}">{badge}</div>
      <div class="efc-meta">{meta}</div>
    </div>
    """


CUSTOM_CSS = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Source+Sans+3:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  .leaflet-container {{
    font-family: "Source Sans 3", "Segoe UI", sans-serif;
    background: #e8e4dc;
  }}
  .leaflet-control-layers {{
    border: 1px solid {CARD_BORDER} !important;
    border-radius: 10px !important;
    box-shadow: 0 4px 18px rgba(28,36,48,.10) !important;
    background: {CARD} !important;
    padding: 6px 8px !important;
    font-size: 12.5px !important;
    color: {INK} !important;
  }}
  .leaflet-control-layers-overlays label {{
    margin: 3px 0 !important;
  }}
  .leaflet-control-zoom a {{
    border-radius: 8px !important;
    color: {INK} !important;
  }}
  .leaflet-popup-content-wrapper {{
    border-radius: 12px !important;
    box-shadow: 0 8px 28px rgba(28,36,48,.16) !important;
    border: 1px solid {CARD_BORDER};
    padding: 0 !important;
    background: #fffdf9;
  }}
  .leaflet-popup-content {{
    margin: 0 !important;
    min-width: 240px;
  }}
  .leaflet-popup-tip {{
    background: #fffdf9;
  }}
  .efc-popup {{ padding: 14px 16px 12px; }}
  .efc-title {{
    font-family: Fraunces, Georgia, serif;
    font-size: 16px;
    font-weight: 700;
    color: {INK};
    line-height: 1.25;
    margin-bottom: 8px;
  }}
  .efc-badge {{
    display: inline-block;
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: .04em;
    text-transform: uppercase;
    color: #fff;
    padding: 3px 8px;
    border-radius: 4px;
    margin-bottom: 10px;
  }}
  .efc-meta {{ border-top: 1px solid #ece7dd; padding-top: 8px; }}
  .meta-row {{
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 3px 0;
    font-size: 12.5px;
    line-height: 1.35;
  }}
  .meta-row .k {{ color: {MUTED}; font-weight: 500; flex: 0 0 42%; }}
  .meta-row .v {{ color: {INK}; text-align: right; flex: 1; }}

  .efc-header {{
    position: fixed; top: 14px; left: 50%; transform: translateX(-50%);
    z-index: 10000; text-align: center;
    background: rgba(247,244,239,.94);
    backdrop-filter: blur(6px);
    border: 1px solid {CARD_BORDER};
    border-radius: 14px;
    padding: 10px 22px 11px;
    box-shadow: 0 6px 22px rgba(28,36,48,.12);
    pointer-events: none;
  }}
  .efc-header h1 {{
    margin: 0;
    font-family: Fraunces, Georgia, serif;
    font-size: 18px;
    font-weight: 700;
    color: {INK};
    letter-spacing: -0.01em;
  }}
  .efc-header p {{
    margin: 3px 0 0;
    font-size: 12px;
    color: {MUTED};
    font-weight: 500;
  }}

  .efc-legend {{
    position: fixed; bottom: 22px; left: 18px; z-index: 9999;
    background: rgba(247,244,239,.96);
    border: 1px solid {CARD_BORDER};
    border-radius: 12px;
    padding: 12px 14px;
    box-shadow: 0 6px 20px rgba(28,36,48,.12);
    max-width: 300px;
    font-size: 12px;
    color: {INK};
  }}
  .efc-legend .ttl {{
    font-family: Fraunces, Georgia, serif;
    font-weight: 700;
    font-size: 13px;
    margin-bottom: 8px;
  }}
  .efc-legend .row {{
    display: flex; align-items: center; gap: 9px;
    margin: 5px 0; color: {MUTED};
  }}
  .efc-legend .row b {{ color: {INK}; font-weight: 600; }}
  .swatch-ews {{
    width: 13px; height: 13px; background: {EWS_FILL};
    transform: rotate(45deg); border: 2px solid #fff;
    box-shadow: 0 1px 3px rgba(0,0,0,.25); flex-shrink: 0;
  }}
  .swatch-ana {{
    width: 13px; height: 13px; background: {ANA_FILL};
    border-radius: 50%; border: 2px solid {ANA_EDGE};
    box-shadow: 0 1px 3px rgba(0,0,0,.2); flex-shrink: 0;
  }}
  .swatch-cat {{
    width: 12px; height: 12px; background: {ANA_CAT};
    border-radius: 50%; border: 2px solid #5c6570;
    flex-shrink: 0;
  }}
  .swatch-line {{
    width: 18px; height: 3px; background: {EFC_LINE};
    border-radius: 2px; flex-shrink: 0;
  }}
  .efc-legend .foot {{
    margin-top: 8px; padding-top: 8px;
    border-top: 1px solid #ece7dd;
    font-size: 11px; color: {MUTED};
  }}

  .efc-compare {{
    position: fixed; top: 78px; right: 14px; z-index: 9999;
    background: rgba(247,244,239,.96);
    border: 1px solid {CARD_BORDER};
    border-radius: 12px;
    padding: 11px 12px;
    box-shadow: 0 6px 20px rgba(28,36,48,.12);
    max-width: 390px; max-height: 42vh; overflow: auto;
    font-size: 11.5px; color: {INK};
  }}
  .efc-compare .ttl {{
    font-family: Fraunces, Georgia, serif;
    font-weight: 700; font-size: 12.5px;
    margin-bottom: 7px;
  }}
  .efc-compare table {{
    width: 100%; border-collapse: collapse;
  }}
  .efc-compare th {{
    text-align: left; color: {MUTED}; font-weight: 600;
    font-size: 10px; text-transform: uppercase; letter-spacing: .03em;
    padding: 4px 5px 6px; border-bottom: 1px solid #ece7dd;
  }}
  .efc-compare td {{
    padding: 5px; vertical-align: top;
    border-bottom: 1px solid #f0ebe3;
  }}
  .efc-compare tr:last-child td {{ border-bottom: none; }}
</style>
"""


def build_html(corridor: dict, path: Path) -> None:
    efc = corridor["efc"]
    ana = corridor["stations"]
    mid = efc[len(efc) // 2]

    m = folium.Map(
        location=[mid["lat"], mid["lon"]],
        zoom_start=6,
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
    )

    # Soft light basemap (Esri) + OSM fallback
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Esri · Light Gray Canvas",
        name="Mapa claro",
        max_zoom=16,
    ).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(m)

    fg_efc = FeatureGroup(name="Eixo EFC", show=True)
    fg_ews = FeatureGroup(name="Pontos EWS / EFC", show=True)
    fg_ana_nivel = FeatureGroup(name="ANA com nível (BQ)", show=True)
    fg_ana_catalog = FeatureGroup(name="ANA catálogo (s/ nível BQ)", show=True)

    folium.PolyLine(
        [[p["lat"], p["lon"]] for p in efc],
        color=EFC_LINE,
        weight=3.5,
        opacity=0.88,
        tooltip="Estrada de Ferro Carajás",
    ).add_to(fg_efc)

    for p in OPS:
        popup = _popup_html(
            p["nome"],
            "EWS / EFC",
            EWS_FILL,
            [
                ("Fonte", p["fonte"]),
                ("Código EWS", p.get("ews") or "—"),
                ("Km EFC", str(p["km_efc"])),
                ("Lat / Lon", f"{p['lat']:.6f}, {p['lon']:.6f}"),
                ("Arquivo", p["xlsx"]),
                ("Variável", "precipitação (pluviômetro)"),
            ],
        )
        icon_html = f"""
        <div style="position:relative;width:34px;height:42px;">
          <div style="
            width:22px;height:22px;background:{EWS_FILL};
            border:2.5px solid #fff;transform:rotate(45deg);
            margin:4px 0 0 6px;
            box-shadow:0 2px 5px rgba(28,36,48,.28);"></div>
        </div>
        """
        folium.Marker(
            [p["lat"], p["lon"]],
            popup=folium.Popup(popup, max_width=340),
            tooltip=f"EWS · {p['nome']}",
            icon=folium.DivIcon(html=icon_html, icon_size=(34, 42), icon_anchor=(17, 17)),
        ).add_to(fg_ews)
        folium.CircleMarker(
            [p["lat"], p["lon"]],
            radius=13,
            color=EWS_EDGE,
            fill=True,
            fill_color=EWS_FILL,
            fill_opacity=0.22,
            weight=1.5,
        ).add_to(fg_ews)

    for s in ana:
        nome_limpo = s["nome"].title() if s["nome"].isupper() else s["nome"]
        label = f"ANA · {nome_limpo}"
        has = s.get("has_nivel_bq")
        fresh = s.get("fresh")
        if has and fresh:
            fill, border, avail = ANA_FILL, ANA_EDGE, "nível recente no BQ"
        elif has:
            fill, border, avail = ANA_STALE, ANA_EDGE, "nível no BQ (pode estar defasado)"
        else:
            fill, border, avail = ANA_CAT, "#5c6570", "catálogo ANA (sem série nível no BQ)"

        nivel_txt = f"{s['last_nivel_m']:.3f} m" if s.get("last_nivel_m") is not None else "—"
        popup = _popup_html(
            label,
            "ANA hidrológica",
            ANA_FILL if has else ANA_CAT,
            [
                ("Código", s["code"]),
                ("Rio", s.get("rio") or "—"),
                ("Município", s.get("municipio") or "—"),
                ("Lat / Lon", f"{s['lat']:.5f}, {s['lon']:.5f}"),
                ("Km EFC", f"{s.get('nearest_efc_km')} ({s.get('dist_efc_km')} km)"),
                ("Variável", "nivel_m (cota)"),
                ("Último nível", nivel_txt),
                ("Última data", s.get("max_date") or "—"),
                ("Disponibilidade", avail),
            ],
        )
        target = fg_ana_nivel if has else fg_ana_catalog
        folium.CircleMarker(
            [s["lat"], s["lon"]],
            radius=11 if has else 9,
            color=border,
            fill=True,
            fill_color=fill,
            fill_opacity=0.92,
            weight=2,
            popup=folium.Popup(popup, max_width=360),
            tooltip=label,
        ).add_to(target)

    fg_efc.add_to(m)
    fg_ews.add_to(m)
    fg_ana_nivel.add_to(m)
    fg_ana_catalog.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    plugins.Fullscreen().add_to(m)

    m.get_root().header.add_child(folium.Element(CUSTOM_CSS))

    header = """
    <div class="efc-header">
      <h1>EFC · Nível — EWS vs ANA</h1>
      <p>Pontos operacionais EWS e estações hidrológicas ANA ao longo da ferrovia</p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(header))

    n_ana = corridor["count_total"]
    n_bq = corridor["count_with_nivel_bq"]
    n_prev = corridor["count_previous_map"]
    legend = f"""
    <div class="efc-legend">
      <div class="ttl">Legenda</div>
      <div class="row"><span class="swatch-ews"></span><span><b>EWS / EFC</b> — abrigos e pontos</span></div>
      <div class="row"><span class="swatch-ana"></span><span><b>ANA</b> — nível no BigQuery</span></div>
      <div class="row"><span class="swatch-cat"></span><span><b>ANA catálogo</b> — sem nível BQ</span></div>
      <div class="row"><span class="swatch-line"></span><span>Eixo da EFC</span></div>
      <div class="foot">ANA no corredor (≤{corridor['buffer_km']} km): <b>{n_ana}</b>
        · antes: {n_prev} · com nível BQ: {n_bq}<br>
        Km 661 (eixo) ≠ vão: ponte OSM ~170&nbsp;m a leste (~km&nbsp;660,8) sobre corredor ripário.</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    cmp = nearest_ana(OPS, ana)
    rows_html = "".join(
        f"<tr><td><b>{r['ponto_ews']}</b></td>"
        f"<td>{r['ana_mais_proxima'].replace('ANA · ','')}</td>"
        f"<td>{r['dist_km']} km</td>"
        f"<td>{r['ana_ultima_data'] or '—'}</td></tr>"
        for r in cmp
    )
    panel = f"""
    <div class="efc-compare">
      <div class="ttl">EWS → ANA mais próxima</div>
      <table>
        <tr><th>Ponto</th><th>ANA</th><th>Dist.</th><th>Data nível</th></tr>
        {rows_html}
      </table>
    </div>
    """
    m.get_root().html.add_child(folium.Element(panel))

    lats = [p["lat"] for p in OPS] + [s["lat"] for s in ana]
    lons = [p["lon"] for p in OPS] + [s["lon"] for s in ana]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(40, 40))
    m.save(str(path))
    print("HTML", path)


def build_png(corridor: dict, path: Path) -> None:
    efc = corridor["efc"]
    ana = corridor["stations"]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.edgecolor": "#cfc7b8",
            "axes.labelcolor": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "text.color": INK,
        }
    )

    fig, ax = plt.subplots(figsize=(13.8, 8.6), facecolor=CARD)
    ax.set_facecolor("#ebe6dc")

    ax.plot(
        [p["lon"] for p in efc],
        [p["lat"] for p in efc],
        color=EFC_LINE,
        lw=2.6,
        zorder=2,
        solid_capstyle="round",
    )

    for s in ana:
        if s.get("has_nivel_bq"):
            c = ANA_FILL if s.get("fresh") else ANA_STALE
            ax.scatter(
                [s["lon"]],
                [s["lat"]],
                s=110,
                c=c,
                zorder=3,
                edgecolors=ANA_EDGE,
                linewidths=0.8,
                alpha=0.95,
            )
        else:
            ax.scatter(
                [s["lon"]],
                [s["lat"]],
                s=80,
                c=ANA_CAT,
                zorder=3,
                edgecolors="#5c6570",
                linewidths=0.7,
            )

    for p in OPS:
        ax.scatter(
            [p["lon"]],
            [p["lat"]],
            s=260,
            c=EWS_FILL,
            marker="D",
            zorder=5,
            edgecolors="white",
            linewidths=1.4,
        )
        ax.annotate(
            p["nome"],
            (p["lon"], p["lat"]),
            textcoords="offset points",
            xytext=(11, 9),
            fontsize=9,
            fontweight="bold",
            color=EWS_EDGE,
            bbox=dict(boxstyle="round,pad=0.22", fc="#fffdf9", ec=CARD_BORDER, alpha=0.92),
        )

    ax.set_title("EFC · Nível — EWS vs ANA", fontsize=16, fontweight="bold", pad=14, color=INK, loc="left")
    ax.text(
        0.0,
        1.01,
        f"{corridor['count_total']} estações ANA no corredor  ·  {len(OPS)} pontos EWS/EFC",
        transform=ax.transAxes,
        fontsize=10,
        color=MUTED,
    )
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(True, color="#d5cec2", alpha=0.7, lw=0.8)
    for spine in ax.spines.values():
        spine.set_color("#cfc7b8")

    handles = [
        Line2D([0], [0], color=EFC_LINE, lw=2.6, label="Eixo EFC"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor=EWS_FILL, markersize=11, label="EWS / ponto EFC"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=ANA_FILL, markersize=10, label="ANA com nível BQ"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=ANA_CAT, markersize=9, label="ANA catálogo s/ nível"),
    ]
    leg = ax.legend(
        handles=handles,
        loc="upper right",
        fontsize=9,
        framealpha=0.96,
        fancybox=True,
        edgecolor=CARD_BORDER,
        facecolor="#fffdf9",
    )
    leg.get_frame().set_linewidth(1.0)

    fig.tight_layout()
    fig.savefig(path, dpi=170, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print("PNG", path)


def build_xlsx(corridor: dict, path: Path) -> None:
    ana = corridor["stations"]
    cmp = nearest_ana(OPS, ana)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame(OPS).to_excel(w, sheet_name="pontos_EWS_EFC", index=False)
        pd.DataFrame(cmp).to_excel(w, sheet_name="comparacao_EWS_vs_ANA", index=False)
        pd.DataFrame(ana).to_excel(w, sheet_name="ANA_corredor_EFC", index=False)
        pd.DataFrame(
            [
                {
                    "buffer_km": corridor["buffer_km"],
                    "ana_total_corredor": corridor["count_total"],
                    "ana_com_nivel_bq": corridor["count_with_nivel_bq"],
                    "ana_fresh_ge_2026_09": corridor["count_fresh"],
                    "ana_mapa_anterior": corridor["count_previous_map"],
                    "pontos_ews_efc": len(OPS),
                }
            ]
        ).to_excel(w, sheet_name="resumo_contagens", index=False)
    print("XLSX", path)


def main() -> None:
    corridor = json.loads(CORRIDOR.read_text())
    build_html(corridor, OUT_HTML)
    build_png(corridor, OUT_PNG)
    build_xlsx(corridor, OUT_XLSX)
    LEGACY_HTML.write_bytes(OUT_HTML.read_bytes())
    LEGACY_PNG.write_bytes(OUT_PNG.read_bytes())
    for dest in (Path("/opt/cursor/artifacts"), Path("/tmp")):
        dest.mkdir(parents=True, exist_ok=True)
        for f in (OUT_HTML, OUT_PNG, OUT_XLSX, LEGACY_HTML, LEGACY_PNG):
            (dest / f.name).write_bytes(f.read_bytes())


if __name__ == "__main__":
    main()
