#!/usr/bin/env python3
"""Gera a figura de arquitetura em 5 etapas (SVG + PNG) — tipografia e ícones grandes."""

from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
SVG_PATH = OUT_DIR / "arquitetura_sistema_5_etapas.svg"
PNG_PATH = OUT_DIR / "arquitetura_sistema_5_etapas.png"

# Layout amplo para tipografia/ícones bem grandes e legíveis
W, H = 2600, 900
MARGIN = 32
PANEL_W = 460
PANEL_H = 830
GAP = 48
Y0 = 35

HEADER_ICON_SCALE = 2.55
ITEM_ICON_SCALE = 2.65
ITEM_ICON_SLOT = 72  # horizontal space reserved for scaled item icon

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
    """Ícones de cabeçalho desenhados em ~32x32 (escalados depois)."""
    c = color
    if kind == "imagery":
        return f"""
      <circle cx="16" cy="16" r="15.2" fill="none" stroke="{c}" stroke-width="2.4"/>
      <rect x="6.5" y="8.5" width="19" height="15" fill="none" stroke="{c}" stroke-width="2"/>
      <path d="M8.5 19.5 L13 14 L16.2 17.2 L19.5 12.5 L23.5 19.5" fill="none" stroke="{c}" stroke-width="2"/>
      <circle cx="11" cy="12" r="1.7" fill="{c}"/>"""
    if kind == "pipe":
        return f"""
      <circle cx="16" cy="16" r="15.2" fill="none" stroke="{c}" stroke-width="2.4"/>
      <rect x="4" y="11.5" width="24" height="9" fill="none" stroke="{c}" stroke-width="2"/>
      <circle cx="10.5" cy="16" r="3.1" fill="none" stroke="{c}" stroke-width="1.9"/>
      <circle cx="21.5" cy="16" r="3.1" fill="none" stroke="{c}" stroke-width="1.9"/>
      <path d="M1 16h3M28 16h3" stroke="{c}" stroke-width="2"/>"""
    if kind == "rack":
        return f"""
      <circle cx="16" cy="16" r="15.2" fill="none" stroke="{c}" stroke-width="2.4"/>
      <rect x="6.5" y="6.5" width="19" height="19" fill="none" stroke="{c}" stroke-width="2"/>
      <path d="M9.5 11.5h13M9.5 16h13M9.5 20.5h13" stroke="{c}" stroke-width="1.8"/>
      <circle cx="11.5" cy="11.5" r="1.3" fill="{c}"/>
      <circle cx="11.5" cy="16" r="1.3" fill="{c}"/>
      <circle cx="11.5" cy="20.5" r="1.3" fill="{c}"/>"""
    if kind == "bars":
        return f"""
      <circle cx="16" cy="16" r="15.2" fill="none" stroke="{c}" stroke-width="2.4"/>
      <path d="M6.5 23.5V13.5h4.5v10M13.75 23.5V9.5h4.5v14M21 23.5V6.5h4.5v17" fill="none" stroke="{c}" stroke-width="2"/>
      <path d="M5.5 11.5h21" stroke="{c}" stroke-width="1.4" stroke-dasharray="2.8 2.2"/>"""
    return f"""
      <circle cx="16" cy="16" r="15.2" fill="none" stroke="{c}" stroke-width="2.4"/>
      <rect x="5.5" y="7.5" width="21" height="14.5" fill="none" stroke="{c}" stroke-width="2"/>
      <path d="M11.5 24h9M16 24v-2" stroke="{c}" stroke-width="1.9"/>
      <path d="M8.5 13.5h9M8.5 17h6" stroke="{c}" stroke-width="1.7"/>"""


def item_icon(kind: str, color: str) -> str:
    c = color
    sw = 2.6
    icons = {
        "frame": f'<rect x="0" y="2" width="24" height="18" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M4 8h16M4 13h12" stroke="{c}" stroke-width="2.3"/>',
        "rain": f'<path d="M2 13 Q2 2 11 2 Q13.5 -1.5 18 2 Q26 2 26 9 Q30 9 30 14 Q30 20 23 20 H7 Q2 20 2 13Z" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M9 22v6M16 22v8M23 22v5" stroke="{c}" stroke-width="2.5"/>',
        "gauge": f'<path d="M12 0v14M3.5 7l8.5 8.5 8.5-8.5" fill="none" stroke="{c}" stroke-width="{sw}"/><circle cx="12" cy="24" r="3.8" fill="none" stroke="{c}" stroke-width="2.5"/>',
        "probe": f'<rect x="2" y="0" width="20" height="26" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M6 7h12M6 13h12M6 19h8" stroke="{c}" stroke-width="2.3"/>',
        "track": f'<path d="M0 5h26M0 13h26M0 21h16" stroke="{c}" stroke-width="{sw}"/><circle cx="22" cy="21" r="3.8" fill="none" stroke="{c}" stroke-width="2.5"/>',
        "upload": f'<path d="M3 20 L13 4 L23 20" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M13 6v20" stroke="{c}" stroke-width="{sw}"/>',
        "db": f'<ellipse cx="13" cy="5" rx="11" ry="4.5" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M2 5v13c0 4.8 22 4.8 22 0V5" fill="none" stroke="{c}" stroke-width="{sw}"/>',
        "qc": f'<path d="M2 4h20M2 12h20M2 20h12" stroke="{c}" stroke-width="{sw}"/><path d="M17 17l4 4 8-10" fill="none" stroke="{c}" stroke-width="2.8"/>',
        "catalog": f'<rect x="1" y="2" width="24" height="20" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M6 8h14M6 14h11" stroke="{c}" stroke-width="2.3"/>',
        "server": f'<rect x="1" y="3" width="24" height="18" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M5 9h16M5 15h11" stroke="{c}" stroke-width="2.3"/>',
        "nodes": f'<circle cx="7" cy="13" r="4" fill="none" stroke="{c}" stroke-width="2.5"/><circle cx="20" cy="5" r="4" fill="none" stroke="{c}" stroke-width="2.5"/><circle cx="20" cy="21" r="4" fill="none" stroke="{c}" stroke-width="2.5"/><path d="M10.5 11.5l6-4.5M10.5 14.5l6 4.5" stroke="{c}" stroke-width="2.3"/>',
        "series": f'<path d="M1 20 L7 10 L13 15 L19 3 L26 9" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M1 23h25" stroke="{c}" stroke-width="2.2"/>',
        "meta": f'<rect x="2" y="2" width="20" height="20" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M7 8h10M7 14h8" stroke="{c}" stroke-width="2.3"/>',
        "trend": f'<path d="M2 21 L9 7 L16 15 L25 2" fill="none" stroke="{c}" stroke-width="2.8"/><circle cx="25" cy="2" r="2.8" fill="{c}"/>',
        "accum": f'<rect x="1" y="2" width="24" height="20" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M6 18V10M11 18V7M16 18V12M21 18V5" stroke="{c}" stroke-width="2.5"/>',
        "prio": f'<path d="M13 1 L3 23h20Z" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M13 9v6M13 18.5v1.8" stroke="{c}" stroke-width="2.7"/>',
        "alert": f'<rect x="1" y="5" width="24" height="15" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M8 5v15M1 12.5h24" stroke="{c}" stroke-width="2.2"/>',
        "map": f'<rect x="0" y="2" width="26" height="20" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M4 16l6-6 5 4 6-7" fill="none" stroke="{c}" stroke-width="2.5"/><circle cx="21" cy="7" r="2.3" fill="{c}"/>',
        "dash": f'<rect x="0" y="1" width="12" height="10" fill="none" stroke="{c}" stroke-width="2.3"/><rect x="14" y="1" width="12" height="10" fill="none" stroke="{c}" stroke-width="2.3"/><rect x="0" y="14" width="26" height="10" fill="none" stroke="{c}" stroke-width="2.3"/>',
        "report": f'<path d="M5 0h14l6 6v18H5Z" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M19 0v6h6M9 13h12M9 18h9" stroke="{c}" stroke-width="2.2"/>',
        "query": f'<circle cx="12" cy="12" r="8.5" fill="none" stroke="{c}" stroke-width="{sw}"/><path d="M18 18l8 8" stroke="{c}" stroke-width="2.8"/>',
    }
    return icons[kind]


def build_svg() -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Arquitetura do sistema em cinco etapas">',
        """  <defs>
    <style type="text/css"><![CDATA[
      .bg { fill: #f1f1ee; }
      .panel { fill: #ffffff; stroke-width: 4; }
      .num {
        font-family: "IBM Plex Mono", Consolas, "Courier New", monospace;
        font-size: 32px; font-weight: 700; letter-spacing: 0.12em;
      }
      .title {
        font-family: "IBM Plex Sans Condensed", "Helvetica Neue", Arial, sans-serif;
        font-size: 30px; font-weight: 800; letter-spacing: 0.02em; fill: #101010;
      }
      .item {
        font-family: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
        font-size: 26px; font-weight: 800; fill: #101010;
      }
      .sub {
        font-family: "IBM Plex Mono", Consolas, "Courier New", monospace;
        font-size: 19.5px; font-weight: 600; fill: #333;
      }
      .arrow { fill: #222; }
    ]]></style>
  </defs>""",
        f'  <rect class="bg" width="{W}" height="{H}"/>',
    ]

    grid = ['  <g stroke="#d8d8d2" stroke-width="0.7" opacity="0.85">']
    for y in range(0, H + 1, 50):
        grid.append(f'    <path d="M0 {y}H{W}"/>')
    for x in range(0, W + 1, 50):
        grid.append(f'    <path d="M{x} 0V{H}"/>')
    grid.append("  </g>")
    parts.extend(grid)

    header_size = 32 * HEADER_ICON_SCALE  # visual size after scale

    for i, panel in enumerate(PANELS):
        color = COLORS[i]
        x = MARGIN + i * (PANEL_W + GAP)
        cx = PANEL_W / 2
        parts.append(f'  <g transform="translate({x},{Y0})">')
        parts.append(
            f'    <rect class="panel" width="{PANEL_W}" height="{PANEL_H}" rx="3" ry="3" stroke="{color}"/>'
        )
        parts.append(f'    <rect width="{PANEL_W}" height="8" fill="{color}"/>')

        # header icon centered, large
        hx = cx - (16 * HEADER_ICON_SCALE)
        parts.append(
            f'    <g transform="translate({hx:.1f},22) scale({HEADER_ICON_SCALE})">'
            f'{header_icon(panel["header_icon"], color)}</g>'
        )

        num_y = 22 + header_size + 34
        title_y = num_y + 34
        rule_y = title_y + 20

        parts.append(
            f'    <text class="num" x="{cx}" y="{num_y:.0f}" text-anchor="middle" fill="{color}">{panel["num"]}</text>'
        )
        parts.append(
            f'    <text class="title" x="{cx}" y="{title_y:.0f}" text-anchor="middle">{esc(panel["title"])}</text>'
        )
        parts.append(
            f'    <line x1="36" y1="{rule_y:.0f}" x2="{PANEL_W - 36}" y2="{rule_y:.0f}" stroke="{color}" stroke-width="2.4"/>'
        )

        n = len(panel["items"])
        top = rule_y + 40
        bottom = PANEL_H - 40
        step = (bottom - top) / max(n, 1)
        text_x = ITEM_ICON_SLOT

        for j, (ikind, label, sub) in enumerate(panel["items"]):
            iy = top + j * step
            parts.append(f'    <g transform="translate(28,{iy:.1f})">')
            parts.append(
                f'      <g transform="translate(0,2) scale({ITEM_ICON_SCALE})">{item_icon(ikind, color)}</g>'
            )
            parts.append(
                f'      <text class="item" x="{text_x}" y="28">{esc(label)}</text>'
            )
            parts.append(
                f'      <text class="sub" x="{text_x}" y="56">{esc(sub)}</text>'
            )
            parts.append("    </g>")
        parts.append("  </g>")

        if i < len(PANELS) - 1:
            ax = x + PANEL_W + 6
            mid = Y0 + PANEL_H / 2
            # seta apontando para a direita (próxima etapa)
            parts.append(
                f'  <polygon class="arrow" points="'
                f"{ax},{mid - 8} "
                f"{ax + 16},{mid - 8} "
                f"{ax + 16},{mid - 18} "
                f"{ax + 40},{mid} "
                f"{ax + 16},{mid + 18} "
                f"{ax + 16},{mid + 8} "
                f"{ax},{mid + 8}"
                f'"/>'
            )

    parts.append(
        f'  <g fill="none" stroke="#222" stroke-width="2" opacity="0.5">'
        f'<path d="M16 16h22M16 16v22"/>'
        f'<path d="M{W - 16} 16h-22M{W - 16} 16v22"/>'
        f'<path d="M16 {H - 16}h22M16 {H - 16}v-22"/>'
        f'<path d="M{W - 16} {H - 16}h-22M{W - 16} {H - 16}v-22"/>'
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
