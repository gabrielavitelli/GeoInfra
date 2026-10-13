#!/usr/bin/env python3
"""Mapa EFC completo: Km 661 + ANA + 3 pontos operacionais (+ infos EWS/xlsx Gabriela)."""
from __future__ import annotations

import json
from pathlib import Path

import folium
from folium import plugins
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd

DATA = Path("/tmp/efc_nivel_pontos_map_data.json")
OUT_HTML = Path("/workspace/Km661_Ponte_Sumauma_mapa_estacoes.html")
OUT_PNG = Path("/workspace/Km661_Ponte_Sumauma_mapa_estacoes.png")
OUT_XLSX = Path("/workspace/EFC_nivel_pontos_resumo.xlsx")
ANA_ORDER = ["23800495", "29040000", "28850000", "29050000"]
ANA_COLORS = {
    "23800495": "#64748b",
    "29040000": "#0c4a6e",
    "28850000": "#0f766e",
    "29050000": "#b45309",
}
OPS_COLORS = {
    "km85": "#7c3aed",
    "sensor_pindare": "#059669",
    "ponte_763_800": "#dc2626",
}


def build_html(data: dict, path: Path) -> None:
    km = data["km661"]
    ana = data["ana_stations"]
    ops = data["ops_points"]
    efc = data["efc"]
    ews = data.get("ews_stats", {})

    m = folium.Map(location=[km["lat"], km["lon"]], zoom_start=6, tiles="OpenStreetMap", control_scale=True)

    folium.PolyLine(
        [[p["lat"], p["lon"]] for p in efc],
        color="#1e3a5f",
        weight=3,
        opacity=0.8,
        tooltip="EFC (eixo a cada 5 km)",
    ).add_to(m)

    # Km 661
    folium.Marker(
        [km["lat"], km["lon"]],
        popup=folium.Popup(
            f"<b>{km['nome']}</b><br>railway_km_661<br>"
            f"Lat/Lon: {km['lat']:.6f}, {km['lon']:.6f}<br>"
            f"EWS próximo: EWS6971 (km 663)",
            max_width=340,
        ),
        tooltip="★ Km 661 · Ponte Sumaúma",
        icon=folium.Icon(color="orange", icon="star", prefix="fa"),
    ).add_to(m)

    # ANA
    for code in ANA_ORDER:
        s = ana[code]
        color = ANA_COLORS[code]
        html = (
            f"<b>{s['nome']}</b><br>ANA {code}<br>Rio: {s.get('rio','')}<br>"
            f"{s.get('municipio','')}<br>Lat/Lon: {s['lat']:.5f}, {s['lon']:.5f}<br>"
            f"Dist. ao Km 661: <b>{s['dist_km_661']:.1f} km</b>"
        )
        folium.CircleMarker(
            [s["lat"], s["lon"]],
            radius=8,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.9,
            popup=folium.Popup(html, max_width=320),
            tooltip=f"ANA {code} · {s['nome']}",
        ).add_to(m)

    # Ops points
    for p in ops:
        color = OPS_COLORS[p["id"]]
        ews_info = ""
        if p.get("ews") and p["ews"] in ews:
            st = ews[p["ews"]]
            ews_info = (
                f"<br><b>EWS {p['ews']}</b> (km {st['km_efc']:.0f})<br>"
                f"Precip série: {st['dmin']} → {st['dmax']} · {st['days']} dias<br>"
                f"Σ mm={st['mm_sum']} · máx diário={st['mm_max']} mm<br>"
                f"xlsx: {', '.join(st['source_xlsx'][:3])}"
            )
        elif p.get("xlsx"):
            ews_info = f"<br>xlsx: {', '.join(p['xlsx'])}"
        html = (
            f"<b>{p['nome']}</b><br>Tipo: {p['tipo']}<br>"
            f"Lat/Lon: {p['lat']:.6f}, {p['lon']:.6f}<br>"
            f"{p.get('nota','')}{ews_info}"
        )
        folium.Marker(
            [p["lat"], p["lon"]],
            popup=folium.Popup(html, max_width=380),
            tooltip=f"◆ {p['nome']}",
            icon=folium.Icon(color="green" if p["id"] == "sensor_pindare" else ("purple" if p["id"] == "km85" else "red"), icon="info-sign"),
        ).add_to(m)
        folium.CircleMarker(
            [p["lat"], p["lon"]],
            radius=10,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            weight=2,
        ).add_to(m)

    legend = """
    <div style="position:fixed;bottom:20px;left:20px;z-index:9999;background:rgba(255,255,255,0.96);
                padding:12px 14px;border-radius:8px;border:1px solid #cbd5e1;font:12px Arial;
                box-shadow:0 2px 8px rgba(0,0,0,.12);max-width:360px;">
      <div style="font-weight:700;margin-bottom:6px;">EFC · pontos de nível / sensores</div>
      <div>★ Km 661 Ponte Sumaúma (BQ)</div>
      <div style="color:#7c3aed;">◆ Km 85 (usuário + EWS6987 xlsx)</div>
      <div style="color:#059669;">◆ Sensor Rio Pindaré (usuário)</div>
      <div style="color:#dc2626;">◆ Ponte 763+800 (usuário + EWS6970 ~km761)</div>
      <div style="margin-top:4px;">● Estações ANA nível (4)</div>
      <div style="margin-top:4px;">━ Eixo EFC</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    lats = [km["lat"]] + [ana[c]["lat"] for c in ANA_ORDER] + [p["lat"] for p in ops]
    lons = [km["lon"]] + [ana[c]["lon"] for c in ANA_ORDER] + [p["lon"] for p in ops]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(30, 30))
    plugins.Fullscreen().add_to(m)
    m.save(str(path))
    print("HTML", path)


def build_png(data: dict, path: Path) -> None:
    km = data["km661"]
    ana = data["ana_stations"]
    ops = data["ops_points"]
    efc = data["efc"]

    fig, ax = plt.subplots(figsize=(12.5, 8.2), facecolor="#f8fafc")
    ax.set_facecolor("#eef2f6")
    ax.plot([p["lon"] for p in efc], [p["lat"] for p in efc], color="#1e3a5f", lw=2.0, zorder=2, label="EFC")

    ax.scatter([km["lon"]], [km["lat"]], s=260, c="#ea580c", marker="*", zorder=6, edgecolors="#7c2d12", label="Km 661 Ponte Sumaúma")
    ax.annotate("Km 661\nPonte Sumaúma", (km["lon"], km["lat"]), textcoords="offset points", xytext=(8, 8), fontsize=8, fontweight="bold", color="#9a3412")

    for code in ANA_ORDER:
        s = ana[code]
        ax.scatter([s["lon"]], [s["lat"]], s=55, c=ANA_COLORS[code], zorder=4, edgecolors="white", linewidths=0.6)
        ax.annotate(f"ANA {code}", (s["lon"], s["lat"]), textcoords="offset points", xytext=(5, 4), fontsize=7, color=ANA_COLORS[code])

    for p in ops:
        color = OPS_COLORS[p["id"]]
        ax.scatter([p["lon"]], [p["lat"]], s=110, c=color, marker="D", zorder=5, edgecolors="white")
        ax.annotate(p["nome"], (p["lon"], p["lat"]), textcoords="offset points", xytext=(8, -10), fontsize=8, fontweight="bold", color=color)

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("EFC — Km 661, pontos operacionais e estações ANA (nível)", fontsize=13, pad=10)
    ax.grid(True, alpha=0.35)
    handles = [
        Line2D([0], [0], color="#1e3a5f", lw=2, label="EFC"),
        Line2D([0], [0], marker="*", color="w", markerfacecolor="#ea580c", markersize=14, label="Km 661 Ponte Sumaúma"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#7c3aed", markersize=9, label="Km 85"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#059669", markersize=9, label="Sensor Rio Pindaré"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#dc2626", markersize=9, label="Ponte 763+800"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#0c4a6e", markersize=8, label="Estações ANA"),
    ]
    ax.legend(handles=handles, loc="upper right", fontsize=8, framealpha=0.95)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print("PNG", path)


def build_resumo_xlsx(data: dict, path: Path) -> None:
    ana_rows = []
    for code in ANA_ORDER:
        s = data["ana_stations"][code]
        ana_rows.append(
            {
                "tipo": "ANA_nivel",
                "codigo": code,
                "nome": s["nome"],
                "lat": s["lat"],
                "lon": s["lon"],
                "dist_km_661": s["dist_km_661"],
                "municipio": s.get("municipio"),
                "rio": s.get("rio"),
            }
        )
    ops_rows = []
    for p in data["ops_points"]:
        ops_rows.append(
            {
                "id": p["id"],
                "nome": p["nome"],
                "lat": p["lat"],
                "lon": p["lon"],
                "tipo": p["tipo"],
                "ews": p.get("ews"),
                "xlsx": "; ".join(p.get("xlsx") or []),
                "nota": p.get("nota"),
            }
        )
    ews_rows = []
    for code, st in data.get("ews_stats", {}).items():
        ews_rows.append({"ews_code": code, **{k: v for k, v in st.items() if k != "source_xlsx"}, "source_xlsx": "; ".join(st["source_xlsx"])})

    xlsx_rows = data["xlsx_gabriela"]["encontrados_no_catalogo_EWS"]
    est = pd.read_excel("/workspace/gabriela_xlsx/estacoes_EFC_recorte.xlsx")

    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame(ops_rows).to_excel(w, sheet_name="pontos_operacionais", index=False)
        pd.DataFrame(ana_rows).to_excel(w, sheet_name="ana_nivel_proximas", index=False)
        pd.DataFrame(
            [
                {
                    "km": data["km661"]["km"],
                    "nome": data["km661"]["nome"],
                    "lat": data["km661"]["lat"],
                    "lon": data["km661"]["lon"],
                }
            ]
        ).to_excel(w, sheet_name="km661", index=False)
        pd.DataFrame(ews_rows).to_excel(w, sheet_name="ews_series_resumo", index=False)
        pd.DataFrame(xlsx_rows).to_excel(w, sheet_name="xlsx_gabriela_EWS", index=False)
        est.to_excel(w, sheet_name="estacoes_EFC_recorte", index=False)
        pd.DataFrame(
            [
                {
                    "nota": data["xlsx_gabriela"]["nota"],
                    "estacoes_recorte_xlsx": data["xlsx_gabriela"]["estacoes_EFC_recorte_xlsx"],
                }
            ]
        ).to_excel(w, sheet_name="fontes", index=False)
    print("XLSX", path)


def main() -> None:
    data = json.loads(DATA.read_text())
    build_html(data, OUT_HTML)
    build_png(data, OUT_PNG)
    build_resumo_xlsx(data, OUT_XLSX)
    for dest in (Path("/opt/cursor/artifacts"), Path("/tmp")):
        dest.mkdir(parents=True, exist_ok=True)
        for f in (OUT_HTML, OUT_PNG, OUT_XLSX):
            (dest / f.name).write_bytes(f.read_bytes())


if __name__ == "__main__":
    main()
