#!/usr/bin/env python3
"""Mapa HTML + PNG: Km 661 Ponte Sumaúma e estações ANA de nível próximas."""
from __future__ import annotations

import json
from pathlib import Path

import folium
from folium import plugins
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

DATA = Path("/tmp/km661_map_data.json")
OUT_HTML = Path("/workspace/Km661_Ponte_Sumauma_mapa_estacoes.html")
OUT_PNG = Path("/workspace/Km661_Ponte_Sumauma_mapa_estacoes.png")
ORDER = ["23800495", "29040000", "28850000", "29050000"]
COLORS = {
    "23800495": "#64748b",
    "29040000": "#0c4a6e",
    "28850000": "#0f766e",
    "29050000": "#b45309",
}


def build_html(data: dict, path: Path) -> None:
    km = data["km661"]
    stations = data["stations"]
    efc = data["efc"]

    m = folium.Map(
        location=[km["lat"], km["lon"]],
        zoom_start=9,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    # EFC corridor segment
    efc_coords = [[p["lat"], p["lon"]] for p in efc]
    folium.PolyLine(
        efc_coords,
        color="#1e3a5f",
        weight=4,
        opacity=0.85,
        tooltip="EFC (km 640–690)",
    ).add_to(m)

    # Km 661 star
    folium.Marker(
        [km["lat"], km["lon"]],
        popup=folium.Popup(
            f"<b>Km 661 — Ponte Sumaúma (EFC)</b><br>"
            f"railway_km_661<br>"
            f"Lat/Lon: {km['lat']:.6f}, {km['lon']:.6f}<br>"
            f"Fonte: BigQuery climaticsystem.efc_clima.locations",
            max_width=320,
        ),
        tooltip="★ Km 661 · Ponte Sumaúma",
        icon=folium.Icon(color="red", icon="star", prefix="fa"),
    ).add_to(m)
    folium.CircleMarker(
        [km["lat"], km["lon"]],
        radius=10,
        color="#c2410c",
        fill=True,
        fill_color="#ea580c",
        fill_opacity=0.9,
        weight=2,
    ).add_to(m)

    for code in ORDER:
        s = stations[code]
        color = COLORS[code]
        html = (
            f"<b>{s['nome']}</b><br>"
            f"Código ANA: <b>{code}</b><br>"
            f"Rio: {s.get('rio','—')}<br>"
            f"{s.get('municipio','')}<br>"
            f"Lat/Lon: {s['lat']:.5f}, {s['lon']:.5f}<br>"
            f"Distância ao Km 661: <b>{s['dist_km']:.1f} km</b>"
        )
        folium.CircleMarker(
            [s["lat"], s["lon"]],
            radius=9,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.9,
            weight=2,
            popup=folium.Popup(html, max_width=320),
            tooltip=f"{code} · {s['nome']} ({s['dist_km']:.1f} km)",
        ).add_to(m)
        folium.Marker(
            [s["lat"], s["lon"]],
            icon=folium.DivIcon(
                html=(
                    f'<div style="font-size:11px;font-weight:700;color:{color};'
                    f'white-space:nowrap;text-shadow:0 0 3px #fff,0 0 3px #fff;">'
                    f"{code}<br>{s['dist_km']:.0f} km</div>"
                )
            ),
        ).add_to(m)
        # dashed connector to km 661
        folium.PolyLine(
            [[km["lat"], km["lon"]], [s["lat"], s["lon"]]],
            color=color,
            weight=1.5,
            opacity=0.45,
            dash_array="6 8",
        ).add_to(m)

    legend_html = """
    <div style="position:fixed;bottom:24px;left:24px;z-index:9999;
                background:rgba(255,255,255,0.95);padding:12px 14px;border-radius:8px;
                border:1px solid #cbd5e1;font-family:Arial,sans-serif;font-size:12px;
                box-shadow:0 2px 8px rgba(0,0,0,.12);max-width:320px;">
      <div style="font-weight:700;margin-bottom:6px;color:#0f172a;">
        Km 661 · Ponte Sumaúma — estações ANA (nível)
      </div>
      <div><span style="color:#c2410c;font-size:14px;">★</span> Km 661 EFC (BQ railway_km_661)</div>
      <div style="margin-top:2px;">━ EFC km 640–690</div>
      <div style="margin-top:6px;color:#64748b;">● 23800495 São Sebastião do TO</div>
      <div style="color:#0c4a6e;">● 29040000 UHE Tucuruí Montante 1</div>
      <div style="color:#0f766e;">● 28850000 Araguatins</div>
      <div style="color:#b45309;">● 29050000 Marabá</div>
      <div style="margin-top:8px;color:#64748b;font-size:11px;">
        Distâncias em linha reta · ANA telemétrica
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # Fit bounds
    lats = [km["lat"]] + [stations[c]["lat"] for c in ORDER]
    lons = [km["lon"]] + [stations[c]["lon"] for c in ORDER]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(40, 40))
    plugins.Fullscreen().add_to(m)
    m.save(str(path))
    print(f"HTML: {path}")


def build_png(data: dict, path: Path) -> None:
    km = data["km661"]
    stations = data["stations"]
    efc = data["efc"]

    fig, ax = plt.subplots(figsize=(11, 8.5), facecolor="#f8fafc")
    ax.set_facecolor("#eef2f6")

    ax.plot(
        [p["lon"] for p in efc],
        [p["lat"] for p in efc],
        color="#1e3a5f",
        lw=2.5,
        label="EFC (km 640–690)",
        zorder=2,
    )
    # mark a few km labels along EFC
    for mark in (640, 650, 661, 670, 680, 690):
        p = next((x for x in efc if x["km"] == mark), None)
        if p:
            ax.annotate(
                f"km {mark}",
                (p["lon"], p["lat"]),
                textcoords="offset points",
                xytext=(4, -10),
                fontsize=7,
                color="#334155",
            )

    ax.scatter(
        [km["lon"]],
        [km["lat"]],
        s=280,
        c="#ea580c",
        marker="*",
        zorder=6,
        edgecolors="#7c2d12",
        linewidths=0.6,
        label="Km 661 · Ponte Sumaúma",
    )
    ax.annotate(
        "Km 661\nPonte Sumaúma",
        (km["lon"], km["lat"]),
        textcoords="offset points",
        xytext=(10, 8),
        fontsize=9,
        fontweight="bold",
        color="#9a3412",
    )

    for code in ORDER:
        s = stations[code]
        color = COLORS[code]
        ax.plot(
            [km["lon"], s["lon"]],
            [km["lat"], s["lat"]],
            color=color,
            lw=1.0,
            ls="--",
            alpha=0.45,
            zorder=1,
        )
        ax.scatter([s["lon"]], [s["lat"]], s=90, c=color, zorder=5, edgecolors="white", linewidths=0.8)
        ax.annotate(
            f"{s['nome']}\n{code} · {s['dist_km']:.0f} km",
            (s["lon"], s["lat"]),
            textcoords="offset points",
            xytext=(8, 6),
            fontsize=8,
            color=color,
            fontweight="bold",
        )

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(
        "Mapa — Km 661 Ponte Sumaúma (EFC) e estações ANA de nível próximas",
        fontsize=13,
        pad=12,
    )
    ax.grid(True, alpha=0.35)
    handles = [
        Line2D([0], [0], color="#1e3a5f", lw=2.5, label="EFC km 640–690"),
        Line2D(
            [0],
            [0],
            marker="*",
            color="w",
            markerfacecolor="#ea580c",
            markersize=14,
            label="Km 661 · Ponte Sumaúma",
        ),
    ]
    for code in ORDER:
        handles.append(
            Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor=COLORS[code],
                markersize=9,
                label=f"{code} ({stations[code]['dist_km']:.0f} km)",
            )
        )
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.95)
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    print(f"PNG: {path}")


def main() -> None:
    data = json.loads(DATA.read_text())
    build_html(data, OUT_HTML)
    build_png(data, OUT_PNG)
    # copies
    for dest in (Path("/opt/cursor/artifacts"), Path("/tmp")):
        dest.mkdir(parents=True, exist_ok=True)
        for f in (OUT_HTML, OUT_PNG):
            target = dest / f.name
            target.write_bytes(f.read_bytes())
            print(f"copied → {target}")


if __name__ == "__main__":
    main()
