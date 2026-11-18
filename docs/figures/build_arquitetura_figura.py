#!/usr/bin/env python3
"""Gera a figura de arquitetura em 5 etapas (SVG + PNG)."""

from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
SVG_PATH = OUT_DIR / "arquitetura_sistema_5_etapas.svg"
PNG_PATH = OUT_DIR / "arquitetura_sistema_5_etapas.png"

# Layout
W, H = 1760, 560
MARGIN = 28
PANEL_W = 308
PANEL_H = 500
GAP = 40  # space between panels (arrow lives here)
Y0 = 30

COLORS = ["#2b6de8", "#178f7a", "#b8941f", "#d97706", "#6d28d9"]

PANELS = [
    {
        "num": "01",
        "title": "FONTES DE DADOS",
        "header_icon": "imagery",
        "items": [
            ("frame", "Satélite / ortofoto", "Esri ~2 m · ortofoto no zoom"),
            ("rain", "Clima obs. / previsto", "INMET · ANA · MERGE · GFS"),
            ("gauge", "Pluviômetros EWS/Orion", "rede ao longo da ferrovia"),
            ("probe", "Sensores Zentra / ZL6", "TEROS · ATMOS · solo/chuva"),
            ("track", "Malha operacional", "km · taludes · trechos"),
        ],
    },
    {
        "num": "02",
        "title": "PIPELINE DE DADOS",
        "header_icon": "pipe",
        "items": [
            ("upload", "Ingestão automática", "EWS 1 h · Zentra 6 h · jobs"),
            ("db", "Object storage + BigQuery", "GCS · tabelas analíticas"),
            ("qc", "Cursors + validação QC", "filtros · tetos pluviométricos"),
            ("catalog", "Catálogos / snapshots", "consulta rápida na app"),
        ],
    },
    {
        "num": "03",
        "title": "NÚCLEO EM NUVEM",
        "header_icon": "rack",
        "items": [
            ("server", "GCP escalável", "Cloud Run · BQ · GCS"),
            ("nodes", "Scheduler + Secrets", "credenciais · cache · jobs"),
            ("series", "Séries multi-fonte", "clima_series · dispositivos"),
            ("meta", "Metadados + app", "estações · sensores · API"),
        ],
    },
    {
        "num": "04",
        "title": "INTELIGÊNCIA DE RISCO",
        "header_icon": "bars",
        "items": [
            ("trend", "Suscetibilidade / anomalias", "análises e modelos"),
            ("accum", "Acumulados + limiares", "24 h · 5 d · 7 d · 30 d"),
            ("prio", "Priorização por km", "espacial · malha ferroviária"),
            ("alert", "Alertas + Raijin IA", "inspeção · gestão de taludes"),
        ],
    },
    {
        "num": "05",
        "title": "MONITOR E DECISÃO",
        "header_icon": "monitor",
        "items": [
            ("map", "Mapas 2D / 3D", "ferrovia · estações · sensores"),
            ("dash", "Dashboards + Sensores", "médias · portas · CSV/PDF"),
            ("report", "Boletins e previsões", "camada climática animada"),
            ("query", "Consulta integrada", "km · túneis · taludes"),
        ],
    },
]


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def header_icon(kind: str, color: str) -> str:
    c = color
    if kind == "imagery":
        return f"""
      <circle cx="16" cy="16" r="16" fill="none" stroke="{c}" stroke-width="2"/>
      <rect x="7" y="9" width="18" height="14" fill="none" stroke="{c}" stroke-width="1.6"/>
      <path d="M9 19 L13 14 L16 17 L19 13 L23 19" fill="none" stroke="{c}" stroke-width="1.5"/>
      <circle cx="11" cy="12" r="1.4" fill="{c}"/>"""
    if kind == "pipe":
        return f"""
      <circle cx="16" cy="16" r="16" fill="none" stroke="{c}" stroke-width="2"/>
      <rect x="5" y="12" width="22" height="8" fill="none" stroke="{c}" stroke-width="1.6"/>
      <circle cx="11" cy="16" r="2.6" fill="none" stroke="{c}" stroke-width="1.45"/>
      <circle cx="21" cy="16" r="2.6" fill="none" stroke="{c}" stroke-width="1.45"/>
      <path d="M2 16h3M27 16h3" stroke="{c}" stroke-width="1.6"/>"""
    if kind == "rack":
        return f"""
      <circle cx="16" cy="16" r="16" fill="none" stroke="{c}" stroke-width="2"/>
      <rect x="7" y="7" width="18" height="18" fill="none" stroke="{c}" stroke-width="1.55"/>
      <path d="M10 12h12M10 16h12M10 20h12" stroke="{c}" stroke-width="1.3"/>
      <circle cx="12" cy="12" r="1.05" fill="{c}"/>
      <circle cx="12" cy="16" r="1.05" fill="{c}"/>
      <circle cx="12" cy="20" r="1.05" fill="{c}"/>"""
    if kind == "bars":
        return f"""
      <circle cx="16" cy="16" r="16" fill="none" stroke="{c}" stroke-width="2"/>
      <path d="M7 23V14h4v9M14 23V10h4v13M21 23V7h4v16" fill="none" stroke="{c}" stroke-width="1.55"/>
      <path d="M6 12h20" stroke="{c}" stroke-width="1" stroke-dasharray="2.2 1.8"/>"""
    # monitor
    return f"""
      <circle cx="16" cy="16" r="16" fill="none" stroke="{c}" stroke-width="2"/>
      <rect x="6" y="8" width="20" height="14" fill="none" stroke="{c}" stroke-width="1.55"/>
      <path d="M12 22h8M16 22v-2" stroke="{c}" stroke-width="1.45"/>
      <path d="M9 14h8M9 17h5" stroke="{c}" stroke-width="1.25"/>"""


def item_icon(kind: str, color: str) -> str:
    c = color
    icons = {
        "frame": f'<rect x="0" y="2" width="22" height="16" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M4 8h14M4 12h10" stroke="{c}" stroke-width="1.35"/>',
        "rain": f'<path d="M3 12 Q3 3 11 3 Q13 0 17 3 Q24 3 24 9 Q28 9 28 13 Q28 18 22 18 H8 Q3 18 3 12Z" fill="none" stroke="{c}" stroke-width="1.5"/><path d="M9 20v5M15 20v7M21 20v4" stroke="{c}" stroke-width="1.45"/>',
        "gauge": f'<path d="M11 1v12M4 7l7 7 7-7" fill="none" stroke="{c}" stroke-width="1.55"/><circle cx="11" cy="22" r="3.2" fill="none" stroke="{c}" stroke-width="1.5"/>',
        "probe": f'<rect x="2" y="1" width="18" height="22" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M6 7h10M6 12h10M6 17h7" stroke="{c}" stroke-width="1.35"/>',
        "track": f'<path d="M1 5h22M1 11h22M1 17h14" stroke="{c}" stroke-width="1.55"/><circle cx="21" cy="17" r="3.2" fill="none" stroke="{c}" stroke-width="1.45"/>',
        "upload": f'<path d="M4 18 L12 5 L20 18" fill="none" stroke="{c}" stroke-width="1.6"/><path d="M12 7v16" stroke="{c}" stroke-width="1.6"/>',
        "db": f'<ellipse cx="12" cy="5" rx="10" ry="4" fill="none" stroke="{c}" stroke-width="1.5"/><path d="M2 5v11c0 4 20 4 20 0V5" fill="none" stroke="{c}" stroke-width="1.5"/>',
        "qc": f'<path d="M3 5h18M3 11h18M3 17h11" stroke="{c}" stroke-width="1.5"/><path d="M17 15l3 3 7-8" fill="none" stroke="{c}" stroke-width="1.65"/>',
        "catalog": f'<rect x="2" y="3" width="20" height="16" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M6 8h12M6 13h9" stroke="{c}" stroke-width="1.35"/>',
        "server": f'<rect x="2" y="4" width="20" height="14" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M6 9h12M6 13h8" stroke="{c}" stroke-width="1.3"/>',
        "nodes": f'<circle cx="7" cy="12" r="3.1" fill="none" stroke="{c}" stroke-width="1.45"/><circle cx="18" cy="5" r="3.1" fill="none" stroke="{c}" stroke-width="1.45"/><circle cx="18" cy="19" r="3.1" fill="none" stroke="{c}" stroke-width="1.45"/><path d="M10 11l5-4M10 13l5 4" stroke="{c}" stroke-width="1.35"/>',
        "series": f'<path d="M1 18 L6 10 L11 14 L16 4 L22 9" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M1 20h22" stroke="{c}" stroke-width="1.3"/>',
        "meta": f'<rect x="3" y="3" width="16" height="16" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M7 8h8M7 12h6" stroke="{c}" stroke-width="1.3"/>',
        "trend": f'<path d="M3 18 L9 7 L15 14 L22 3" fill="none" stroke="{c}" stroke-width="1.65"/><circle cx="22" cy="3" r="2.1" fill="{c}"/>',
        "accum": f'<rect x="2" y="3" width="20" height="16" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M6 15V9M10 15V7M14 15V10M18 15V6" stroke="{c}" stroke-width="1.45"/>',
        "prio": f'<path d="M12 3 L4 19h16Z" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M12 9v5M12 16.2v1.2" stroke="{c}" stroke-width="1.55"/>',
        "alert": f'<rect x="2" y="5" width="20" height="12" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M7 5v12M2 11h20" stroke="{c}" stroke-width="1.25"/>',
        "map": f'<rect x="1" y="3" width="22" height="16" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M4 14l5-5 4 3 5-6" fill="none" stroke="{c}" stroke-width="1.45"/><circle cx="18" cy="7" r="1.6" fill="{c}"/>',
        "dash": f'<rect x="1" y="2" width="10" height="8" fill="none" stroke="{c}" stroke-width="1.45"/><rect x="13" y="2" width="10" height="8" fill="none" stroke="{c}" stroke-width="1.45"/><rect x="1" y="13" width="22" height="8" fill="none" stroke="{c}" stroke-width="1.45"/>',
        "report": f'<path d="M5 2h12l5 5v15H5Z" fill="none" stroke="{c}" stroke-width="1.5"/><path d="M17 2v5h5M8 12h10M8 16h8" stroke="{c}" stroke-width="1.3"/>',
        "query": f'<circle cx="11" cy="11" r="7" fill="none" stroke="{c}" stroke-width="1.55"/><path d="M16 16l6 6" stroke="{c}" stroke-width="1.55"/>',
    }
    return icons[kind]


def build_svg() -> str:
    parts = [
        f'<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Arquitetura do sistema em cinco etapas">',
        """  <defs>
    <style type="text/css"><![CDATA[
      .bg { fill: #f1f1ee; }
      .panel { fill: #ffffff; stroke-width: 2.25; }
      .num {
        font-family: "IBM Plex Mono", Consolas, "Courier New", monospace;
        font-size: 12px; font-weight: 700; letter-spacing: 0.12em;
      }
      .title {
        font-family: "IBM Plex Sans Condensed", "Helvetica Neue", Arial, sans-serif;
        font-size: 14px; font-weight: 700; letter-spacing: 0.045em; fill: #141414;
      }
      .item {
        font-family: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
        font-size: 12.5px; font-weight: 600; fill: #1c1c1c;
      }
      .sub {
        font-family: "IBM Plex Mono", Consolas, "Courier New", monospace;
        font-size: 10.25px; fill: #5a5a5a;
      }
      .arrow { fill: #2e2e2e; }
    ]]></style>
  </defs>""",
        f'  <rect class="bg" width="{W}" height="{H}"/>',
    ]

    # grid
    grid = ['  <g stroke="#d8d8d2" stroke-width="0.55" opacity="0.9">']
    for y in range(0, H + 1, 45):
        grid.append(f'    <path d="M0 {y}H{W}"/>')
    for x in range(0, W + 1, 45):
        grid.append(f'    <path d="M{x} 0V{H}"/>')
    grid.append("  </g>")
    parts.extend(grid)

    for i, panel in enumerate(PANELS):
        color = COLORS[i]
        x = MARGIN + i * (PANEL_W + GAP)
        cx = PANEL_W / 2
        parts.append(f'  <g transform="translate({x},{Y0})">')
        parts.append(
            f'    <rect class="panel" width="{PANEL_W}" height="{PANEL_H}" rx="2" ry="2" stroke="{color}"/>'
        )
        parts.append(f'    <rect width="{PANEL_W}" height="5" fill="{color}"/>')
        parts.append(f'    <g transform="translate({cx - 16},24)">{header_icon(panel["header_icon"], color)}</g>')
        parts.append(
            f'    <text class="num" x="{cx}" y="78" text-anchor="middle" fill="{color}">{panel["num"]}</text>'
        )
        parts.append(
            f'    <text class="title" x="{cx}" y="100" text-anchor="middle">{esc(panel["title"])}</text>'
        )
        parts.append(
            f'    <line x1="36" y1="114" x2="{PANEL_W - 36}" y2="114" stroke="{color}" stroke-width="1.15"/>'
        )

        n = len(panel["items"])
        # distribute items in remaining vertical space
        top = 140
        bottom = PANEL_H - 28
        step = (bottom - top) / max(n, 1)
        for j, (ikind, label, sub) in enumerate(panel["items"]):
            iy = top + j * step
            parts.append(f'    <g transform="translate(26,{iy:.1f})">')
            parts.append(f"      {item_icon(ikind, color)}")
            parts.append(f'      <text class="item" x="36" y="10">{esc(label)}</text>')
            parts.append(f'      <text class="sub" x="36" y="25">{esc(sub)}</text>')
            parts.append("    </g>")
        parts.append("  </g>")

        # arrow after panel (except last)
        if i < len(PANELS) - 1:
            ax = x + PANEL_W + 6
            mid = Y0 + PANEL_H / 2
            parts.append(
                f'  <polygon class="arrow" points="{ax},{mid} {ax+14},{mid-12} {ax+14},{mid-5} {ax+28},{mid-5} {ax+28},{mid+5} {ax+14},{mid+5} {ax+14},{mid+12}"/>'
            )

    # small corner marks — raw/technical feel
    parts.append(
        f'  <g fill="none" stroke="#222" stroke-width="1.4" opacity="0.55">'
        f'<path d="M12 12h16M12 12v16"/>'
        f'<path d="M{W-12} 12h-16M{W-12} 12v16"/>'
        f'<path d="M12 {H-12}h16M12 {H-12}v-16"/>'
        f'<path d="M{W-12} {H-12}h-16M{W-12} {H-12}v-16"/>'
        f"</g>"
    )

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    svg = build_svg()
    SVG_PATH.write_text(svg, encoding="utf-8")
    print(f"Wrote {SVG_PATH}")

    try:
        import cairosvg

        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(PNG_PATH), scale=2)
        print(f"Wrote {PNG_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"PNG export failed: {exc}")
        # fallback via chrome headless
        import subprocess
        import tempfile

        html = f"""<!DOCTYPE html><html><body style="margin:0;background:#f1f1ee">
        <img src="file://{SVG_PATH}" width="{W}" height="{H}"/>
        </body></html>"""
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write(html)
            html_path = f.name
        subprocess.run(
            [
                "google-chrome",
                "--headless=new",
                "--disable-gpu",
                f"--screenshot={PNG_PATH}",
                f"--window-size={W},{H}",
                f"file://{html_path}",
            ],
            check=False,
        )
        print(f"Chrome fallback -> {PNG_PATH}")


if __name__ == "__main__":
    main()
