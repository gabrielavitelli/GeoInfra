"""Hero operacional da Home — leitura em poucos segundos."""
from __future__ import annotations

import html

import pandas as pd


def _val_col(df) -> str:
    for c in ("Valor (mm)", "Valor"):
        if c in df.columns:
            return c
    return "Valor (mm)"


def _km_col(df) -> str:
    for c in ("Quilometragem (km)", "Quilometragem"):
        if c in df.columns:
            return c
    return "Quilometragem (km)"


def _lim_col(df) -> str:
    return "Limiar" if "Limiar" in df.columns else "Limiar"


def _hero_peak_metric_label(kind: str, *, lang_pt: bool) -> str:
    if kind == "24h":
        return "Pico 24 h" if lang_pt else "Peak 24 h"
    if kind == "5d":
        return "Acum. 5 d" if lang_pt else "Accum. 5 d"
    if kind == "7d":
        return "Acum. 7 d" if lang_pt else "Accum. 7 d"
    if kind == "30d":
        return "Acum. 30 d" if lang_pt else "Accum. 30 d"
    if kind == "raijin_tp":
        return "Raijin TP"
    if kind == "raijin_fp":
        return "Raijin FP"
    if kind == "raijin":
        return "Raijin"
    return "Pico 24 h" if lang_pt else "Peak 24 h"


def status_hero_context(
    *,
    lang_pt: bool,
    selected_display: str,
    status_normal: bool,
    n_crit_day: int,
    df_day: pd.DataFrame | None,
    alerts_source: str | None = None,
) -> dict:
    """Campos para o template do hero (Situação)."""
    peak_mm: float | None = None
    peak_km: int | None = None
    peak_metric: str = ""
    has_severe = False
    src = (alerts_source or "").strip().lower()

    if df_day is not None and not df_day.empty:
        vcol, kcol, lcol = _val_col(df_day), _km_col(df_day), _lim_col(df_day)
        from lib_efc import is_raijin_limiar_kind, limiar_kind_from_label

        raijin_rows = False
        if lcol in df_day.columns:
            kinds = df_day[lcol].map(lambda x: limiar_kind_from_label(str(x)))
            raijin_rows = bool(kinds.map(is_raijin_limiar_kind).any())
            has_severe = any(k in ("5d", "7d", "30d") for k in kinds.dropna())
        use_sum = src == "raijin" or raijin_rows

        if use_sum:
            from monitor_web.services.suscetibilidade import alert_km_if_single, sum_unique_alert_mm

            peak_mm = sum_unique_alert_mm(df_day)
            peak_km = alert_km_if_single(df_day)
            peak_metric = "Raijin"
        elif vcol in df_day.columns:
            s = pd.to_numeric(df_day[vcol], errors="coerce")
            if s.notna().any():
                idx = s.idxmax()
                row = df_day.loc[idx]
                if pd.notna(row.get(vcol)):
                    peak_mm = float(row[vcol])
                if kcol in df_day.columns and pd.notna(row.get(kcol)):
                    peak_km = int(round(float(row[kcol])))
                if lcol in df_day.columns:
                    peak_metric = _hero_peak_metric_label(
                        limiar_kind_from_label(str(row.get(lcol, ""))),
                        lang_pt=lang_pt,
                    )

    if status_normal:
        level = "ok"
        if lang_pt:
            headline = "Normal"
            if src == "raijin":
                subline = f"Nenhum alerta Raijin · {selected_display}"
            else:
                subline = f"Nenhum trecho acima do limiar · {selected_display}"
        else:
            headline = "Normal"
            if src == "raijin":
                subline = f"No Raijin alerts · {selected_display}"
            else:
                subline = f"No corridor exceedances · {selected_display}"
    elif has_severe or n_crit_day >= 5:
        level = "critical"
        if lang_pt:
            headline = "Crítico"
            unit = "alerta(s) Raijin" if src == "raijin" else "trecho(s) em alerta"
            subline = f"{n_crit_day} {unit} · {selected_display}"
        else:
            headline = "Critical"
            unit = "Raijin alert(s)" if src == "raijin" else "corridor segment(s) in alert"
            subline = f"{n_crit_day} {unit} · {selected_display}"
    else:
        level = "watch"
        if lang_pt:
            headline = "Atenção"
            unit = "alerta(s) Raijin" if src == "raijin" else "trecho(s) em alerta"
            subline = f"{n_crit_day} {unit} · {selected_display}"
        else:
            headline = "Attention"
            unit = "Raijin alert(s)" if src == "raijin" else "segment(s) in alert"
            subline = f"{n_crit_day} {unit} · {selected_display}"

    metric = peak_metric or ("Pico 24 h" if lang_pt else "Peak 24 h")
    peak_line = ""
    if peak_mm is not None and peak_km is not None:
        peak_line = f"{peak_mm:.1f} mm · km {peak_km}"
        if src != "raijin" and not (peak_metric or "").startswith("Raijin"):
            peak_line = f"{metric} · {peak_line}"
    elif peak_mm is not None:
        peak_line = f"{peak_mm:.1f} mm"
        if src != "raijin" and not (peak_metric or "").startswith("Raijin"):
            peak_line = f"{metric} · {peak_line}"

    return {
        "hero_level": level,
        "hero_headline": headline,
        "hero_subline": subline,
        "hero_peak_line": peak_line,
        "hero_peak_mm": peak_mm,
        "hero_peak_km": peak_km,
        "hero_n_crit": n_crit_day,
    }


def status_hero_html(ctx: dict) -> str:
    """HTML do hero (legado)."""
    level = html.escape(str(ctx.get("hero_level") or "ok"))
    headline = html.escape(str(ctx.get("hero_headline") or ""))
    subline = html.escape(str(ctx.get("hero_subline") or ""))
    peak = str(ctx.get("hero_peak_line") or "").strip()
    peak_html = f'<p class="efc-hero__peak">{html.escape(peak)}</p>' if peak else ""
    n_crit = int(ctx.get("hero_n_crit") or 0)
    stat_html = f'<span class="efc-hero__stat">{n_crit}</span>' if n_crit > 0 else ""

    return (
        f'<section class="efc-hero efc-hero--{level}" aria-live="polite">'
        f'<div class="efc-hero__main">'
        f'<span class="efc-hero__dot" aria-hidden="true"></span>'
        f'<div class="efc-hero__text">'
        f'<h2 class="efc-hero__headline">{headline}</h2>'
        f'<p class="efc-hero__sub">{subline}</p>'
        f"{peak_html}"
        f"</div>{stat_html}</div></section>"
    )
