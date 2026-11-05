"""Contexto para templates Django dos indicadores (sem HTML em Python)."""
from __future__ import annotations

import pandas as pd


def _sev_tone(*, n_crit: int, pct: float, crit_pct: float) -> str:
    if n_crit == 0:
        return "calm"
    if pct > crit_pct:
        return "crit"
    return "warn"


def _peak_tone(mm, th: float) -> str:
    if mm is None or not pd.notna(mm):
        return "calm"
    return "crit" if float(mm) >= float(th) else "calm"


def _station_lookup_tables(dataset: str | None) -> tuple[dict, dict, dict]:
    try:
        from lib_efc import load_ana_estacoes, load_bndmet_estacoes, load_orion_estacoes
        from monitor_web.services.home_impact_enrich import _build_station_lookup

        bnd = _build_station_lookup(load_bndmet_estacoes(bq_dataset=dataset or None))
        ana = _build_station_lookup(load_ana_estacoes(bq_dataset=dataset or None))
        ews = _build_station_lookup(load_orion_estacoes(bq_dataset=dataset or None))
        return bnd, ana, ews
    except Exception:
        return {}, {}, {}


def _station_display_name(
    *,
    source: str | None,
    station_id: str | None,
    bnd_lookup: dict,
    ana_lookup: dict,
    ews_lookup: dict | None = None,
) -> str:
    sid = str(station_id or "").strip()
    if not sid:
        return "—"
    src = (source or "").lower()
    if src in ("ana",):
        net, table = "ANA", ana_lookup
    elif src in ("orion", "ews"):
        net, table = "EWS", (ews_lookup or {})
    else:
        # Heuristic: EWS*/BS*/BC* codes from Orion even if source missing.
        if sid.upper().startswith(("EWS", "BS", "BC")) and not sid.upper().startswith("IMEI"):
            net, table = "EWS", (ews_lookup or {})
        else:
            net, table = "BNDMET", bnd_lookup
    try:
        from monitor_web.services.home_impact_enrich import _code_variants

        for key in _code_variants(sid):
            if key in table:
                nome = str(table[key].get("name") or "").strip()
                if nome:
                    return f"{nome} · {sid}"
    except Exception:
        pass
    return f"{net} {sid}"


def _clean_station_name(name: str | None) -> str:
    clean = str(name or "").strip()
    if not clean:
        return ""
    for pref in ("BNDMET", "ANA", "INMET", "EWS"):
        if clean.upper().startswith(pref):
            clean = clean[len(pref) :].lstrip(" -:·|/").strip() or clean
            break
    return clean


def _entorno_station_label(
    *,
    source: str | None,
    station_id: str | None,
    station_name: str | None,
    municipio: str | None,
    dist_km,
    bnd_lookup: dict,
    ana_lookup: dict,
    ews_lookup: dict | None = None,
    lang_pt: bool,
) -> str:
    sid = str(station_id or "").strip()
    nome = _clean_station_name(station_name)
    if not nome and sid:
        nome = _clean_station_name(
            _station_display_name(
                source=source,
                station_id=sid,
                bnd_lookup=bnd_lookup,
                ana_lookup=ana_lookup,
                ews_lookup=ews_lookup,
            )
        )
        # Prefer short name; keep code if that is all we have.
        if " · " in nome:
            nome = nome.split(" · ", 1)[0].strip()
    if not nome:
        nome = sid or "—"
    parts = [nome]
    if sid and sid not in nome:
        parts.append(sid)
    if dist_km is not None and pd.notna(dist_km):
        try:
            d = float(dist_km)
            parts.append(f"{d:.0f} km")
        except (TypeError, ValueError):
            pass
    return " · ".join(parts)


def build_indicators_context(
    *,
    lang_pt: bool,
    impact: dict,
    th24: float,
    th120: float,
    th7: float = 150.0,
    th30: float = 465.0,
    segment_lookup: list | None = None,
    exceed_susc_stats: dict[str, dict[str, int]] | None = None,
    km_max: int = 877,
    raijin_alert_kms: int | None = None,
    accum_hours: int | None = None,
    bq_dataset: str | None = None,
) -> dict:
    from monitor_web.services.accum_window import (
        DEFAULT_ACCUM_HOURS,
        accum_kind,
        accum_label,
        parse_accum_hours,
    )

    if not impact.get("available"):
        return {"available": False}

    hours = parse_accum_hours(
        accum_hours if accum_hours is not None else impact.get("station_obs_hours") or DEFAULT_ACCUM_HOURS
    )
    active_kind = accum_kind(hours)
    hours_lbl = accum_label(hours, lang_pt=lang_pt)

    panel = impact.get("station_panel_peaks") or {}
    entorno_panel = impact.get("station_panel_entorno_peaks") or {}
    bnd_lookup, ana_lookup, ews_lookup = _station_lookup_tables(bq_dataset)

    def _panel_row(kind: str) -> dict:
        row = panel.get(kind) if isinstance(panel, dict) else None
        return row if isinstance(row, dict) else {}

    def _entorno_row(kind: str) -> dict:
        row = entorno_panel.get(kind) if isinstance(entorno_panel, dict) else None
        return row if isinstance(row, dict) else {}

    th_by_kind = {"24h": th24, "5d": th120, "7d": th7, "30d": th30}
    env_n = {
        "24h": "n_km_24_envelope",
        "5d": "n_km_120_envelope",
        "7d": "n_km_7d_envelope",
        "30d": "n_km_30d_envelope",
    }
    n_raijin = int(raijin_alert_kms) if raijin_alert_kms is not None else int(
        impact.get("raijin_alert_kms") or 0
    )
    corridor = max(1, int(km_max or 877))
    station_peaks: dict[str, dict] = {}
    railway_kinds: list[str] = []
    for kind in ("24h", "5d", "7d", "30d"):
        row = _panel_row(kind)
        n_above_st = int(row.get("n_above") or 0)
        n_total_st = int(row.get("n_total") or 0)
        rail_n = int(impact.get(env_n[kind]) or 0)
        if kind == "24h":
            rail_n = max(rail_n, n_raijin)
        mm = row.get("mm")
        try:
            mm_f = float(mm) if mm is not None and pd.notna(mm) else 0.0
        except (TypeError, ValueError):
            mm_f = 0.0
        sid = row.get("station_id")
        src = row.get("source")
        # Cards de mm: sempre o maior acumulado observado (BNDMET/ANA).
        # Contagens "acima do limiar": se nenhuma estação passou, usa trechos ferrovia/Raijin.
        use_rail_counts = n_above_st == 0 and rail_n > 0
        if use_rail_counts:
            railway_kinds.append(kind)
            n_above = rail_n
            n_total = corridor
            unit = "km"
            pct = 100.0 * rail_n / corridor
        else:
            n_total = max(1, n_total_st)
            n_above = n_above_st
            unit = "est."
            pct = 100.0 * n_above_st / n_total
        station_peaks[kind] = {
            "mm": mm_f,
            "station_id": sid,
            "source": src,
            "station": _station_display_name(
                source=src,
                station_id=sid,
                bnd_lookup=bnd_lookup,
                ana_lookup=ana_lookup,
                ews_lookup=ews_lookup,
            )
            if sid
            else None,
            "km": None,
            "n_above": n_above,
            "n_total": n_total,
            "pct": pct,
            "th": th_by_kind[kind],
            "unit": unit,
        }

    # Card "Estação Meteorológica" = maior acumulado da janela ativa (toolbar).
    # Contagens em km (sem estação acima do limiar) não substituem o mm observado.
    active_row = station_peaks.get(active_kind) or {}
    obs_station: dict = {}
    if active_row.get("station") and active_row.get("mm") is not None:
        obs_mm = active_row["mm"]
        obs_name = str(active_row.get("station") or "").strip() or "—"
    else:
        b_mm = impact.get("bndmet_max_24_mm")
        a_mm = impact.get("ana_max_24_mm")
        b_ok = b_mm is not None and pd.notna(b_mm)
        a_ok = a_mm is not None and pd.notna(a_mm)

        use_ana = False
        if b_ok and a_ok:
            use_ana = float(a_mm) > float(b_mm)
        elif a_ok and not b_ok:
            use_ana = True

        if use_ana:
            obs_station = impact.get("ana_station") or {}
            obs_mm = a_mm
            obs_id = str(impact.get("ana_max_24_id") or "").strip()
            obs_name = str(obs_station.get("primary") or "").strip() or (
                f"ANA {obs_id}" if obs_id else "ANA"
            )
        else:
            obs_station = impact.get("bndmet_station") or {}
            obs_mm = b_mm if b_ok else None
            obs_id = str(impact.get("bndmet_max_24_id") or "").strip()
            peak_src = str(impact.get("bndmet_max_24_source") or "").strip().lower()
            # EWS peaks sometimes land on the legacy bndmet_* keys — never label as BNDMET.
            if peak_src in ("ews", "orion") or (
                obs_id.upper().startswith(("EWS", "BS", "BC")) and "IMEI" not in obs_id.upper()
            ):
                obs_name = str(obs_station.get("primary") or "").strip() or (
                    _station_display_name(
                        source="ews",
                        station_id=obs_id,
                        bnd_lookup=bnd_lookup,
                        ana_lookup=ana_lookup,
                        ews_lookup=ews_lookup,
                    )
                    if obs_id
                    else "EWS"
                )
            else:
                obs_name = str(obs_station.get("primary") or "").strip() or (
                    f"BNDMET {obs_id}" if obs_id else ("BNDMET" if b_ok else "—")
                )
    # Keep station line short: name only (period already on the card title).
    if " · " in str(obs_name):
        # Drop redundant secondary crumbs like "cód." / hours suffix when present.
        head = str(obs_name).split(" · ", 1)[0].strip()
        if head:
            obs_name = head

    entorno_mode = str(impact.get("entorno_mode") or "corridor")
    try:
        entorno_radius = float(impact.get("entorno_radius_km") or 40.0)
    except (TypeError, ValueError):
        entorno_radius = 40.0
    entorno_focus_km = impact.get("entorno_focus_km")
    try:
        entorno_focus_km_i = int(entorno_focus_km) if entorno_focus_km is not None else None
    except (TypeError, ValueError):
        entorno_focus_km_i = None

    active_ent = _entorno_row(active_kind)
    entorno_mm = active_ent.get("mm")
    try:
        entorno_mm_f = float(entorno_mm) if entorno_mm is not None and pd.notna(entorno_mm) else None
    except (TypeError, ValueError):
        entorno_mm_f = None
    if entorno_mm_f is not None and float(entorno_mm_f) <= 0.0 and not active_ent.get("station_id"):
        entorno_mm_f = None
    entorno_name = None
    if active_ent.get("station_id"):
        entorno_name = _entorno_station_label(
            source=active_ent.get("source"),
            station_id=active_ent.get("station_id"),
            station_name=active_ent.get("station_name"),
            municipio=active_ent.get("municipio"),
            dist_km=None,
            bnd_lookup=bnd_lookup,
            ana_lookup=ana_lookup,
            ews_lookup=ews_lookup,
            lang_pt=lang_pt,
        )

    if lang_pt:
        labels = {
            "obs": "Observado",
            "sev_group": "Acima do limiar",
            "details": "Detalhes técnicos",
            "empty": "—",
            "raijin_metric": "Raijin",
            "raijin_unit": "km",
            "raijin_sub": "24 h",
            "accum_focus": hours_lbl,
            "unit_stations": "est.",
            "entorno": "Entorno",
            "station": "Estação",
        }
        if entorno_mode == "km" and entorno_focus_km_i is not None:
            labels["entorno_focus"] = f"km {entorno_focus_km_i} · {entorno_radius:.0f} km"
        else:
            labels["entorno_focus"] = f"EFC · {entorno_radius:.0f} km"
        peak_defs = (
            ("24 h", "24 h", "pico", "24h"),
            ("5 D", "5 d", "acum", "5d"),
            ("7 d", "7 d", "acum", "7d"),
            ("30 d", "30 d", "acum", "30d"),
        )
        sev_defs = (
            ("24 h", "24 h", "24h"),
            ("5 D", "5 d", "5d"),
            ("7 d", "7 d", "7d"),
            ("30 d", "30 d", "30d"),
        )
    else:
        labels = {
            "obs": "Observed",
            "sev_group": "Above threshold",
            "details": "Technical details",
            "empty": "—",
            "raijin_metric": "Raijin",
            "raijin_unit": "km",
            "raijin_sub": "24 h",
            "accum_focus": hours_lbl,
            "unit_stations": "stn.",
            "entorno": "Area",
            "station": "Station",
        }
        if entorno_mode == "km" and entorno_focus_km_i is not None:
            labels["entorno_focus"] = f"km {entorno_focus_km_i} · {entorno_radius:.0f} km"
        else:
            labels["entorno_focus"] = f"EFC · {entorno_radius:.0f} km"
        peak_defs = (
            ("24 h", "24 h", "pico", "24h"),
            ("5 D", "5 d", "acum", "5d"),
            ("7 d", "7 d", "acum", "7d"),
            ("30 d", "30 d", "acum", "30d"),
        )
        sev_defs = (
            ("24 h", "24 h", "24h"),
            ("5 D", "5 d", "5d"),
            ("7 d", "7 d", "7d"),
            ("30 d", "30 d", "30d"),
        )

    if len(railway_kinds) == 4:
        labels["sev_group"] = "Acima do limiar" if lang_pt else "Above threshold"
        labels["unit_stations"] = "km"
    elif railway_kinds:
        labels["sev_group"] = "Acima do limiar" if lang_pt else "Above threshold"

    period_label = (impact.get("period_label_pt") if lang_pt else impact.get("period_label")) or ""
    period_label = str(period_label).strip()

    peaks = []
    for short, metric, kind, kind_key in peak_defs:
        sp = station_peaks[kind_key]
        peaks.append(
            {
                "label": short,
                "metric": metric,
                "kind": kind,
                "accum_key": kind_key,
                "active": kind_key == active_kind,
                "mm": float(sp["mm"]),
                "km": sp.get("km"),
                "station": sp.get("station"),
                "tone": _peak_tone(sp["mm"], sp["th"]),
            }
        )
    peaks.sort(key=lambda p: (0 if p.get("active") else 1))

    severity = []
    crit_pct_by = {"24h": 15.0, "5d": 5.0, "7d": 5.0, "30d": 3.0}
    for short, sev_label, kind_key in sev_defs:
        sp = station_peaks[kind_key]
        n = int(sp["n_above"])
        total = int(sp["n_total"])
        pct = float(sp["pct"])
        severity.append(
            {
                "label": sev_label,
                "short": short,
                "accum_key": kind_key,
                "active": kind_key == active_kind,
                "pct": pct,
                "n": n,
                "total": total,
                "unit": sp.get("unit") if sp.get("unit") == "km" else labels["unit_stations"],
                "tone": _sev_tone(n_crit=n, pct=pct, crit_pct=crit_pct_by[kind_key]),
                "susc_sub": None,
            }
        )
    severity.sort(key=lambda s: (0 if s.get("active") else 1))

    compact_severity = [s for s in severity if s.get("short") in ("24 h", "7 d", "30 d")]

    entorno_th = float(th_by_kind.get(active_kind) or th24)
    return {
        "available": True,
        "lang_pt": lang_pt,
        "labels": labels,
        "period_label": period_label,
        "accum_hours": hours,
        "accum_kind": active_kind,
        "accum_label": hours_lbl,
        "peaks": peaks,
        "obs": {
            "mm": float(obs_mm) if obs_mm is not None and pd.notna(obs_mm) else None,
            "name": obs_name,
            "hours": hours,
            "hours_label": hours_lbl,
        },
        "entorno": {
            "mm": entorno_mm_f,
            "name": entorno_name,
            "mode": entorno_mode,
            "radius_km": entorno_radius,
            "focus_km": entorno_focus_km_i,
            "focus_label": labels.get("entorno_focus"),
            "tone": _peak_tone(entorno_mm_f, entorno_th) if entorno_mm_f is not None else "calm",
            "n_in_radius": int(active_ent.get("n_in_radius") or 0),
        },
        "raijin": {
            "n_km": n_raijin,
            "tone": "crit" if n_raijin > 0 else "calm",
            "metric": labels["raijin_metric"],
            "unit": labels["raijin_unit"],
            "sub": labels["raijin_sub"],
        },
        "severity": severity,
        "compact_severity": compact_severity,
        "technical": _technical_context(lang_pt=lang_pt, impact=impact, th24=th24),
    }


def _technical_context(*, lang_pt: bool, impact: dict, th24: float) -> dict:
    th24_i = int(th24)
    pct_ag2 = float(impact.get("pct_km_agree_2plus") or 0)
    pct_ag3 = float(impact.get("pct_km_agree_3plus") or 0)
    pct_2 = float(impact.get("pct_km_24_two_models") or 0)
    pct_3 = float(impact.get("pct_km_24_three_models") or 0)
    if lang_pt:
        rows = (
            ("≥2 modelos, mesma faixa", pct_ag2),
            ("≥3 modelos, mesma faixa", pct_ag3),
            (f"≥2 modelos acima de {th24_i} mm", pct_2),
            (f"≥3 modelos acima de {th24_i} mm", pct_3),
        )
    else:
        rows = (
            ("≥2 models, same band", pct_ag2),
            ("≥3 models, same band", pct_ag3),
            (f"≥2 models above {th24_i} mm", pct_2),
            (f"≥3 models above {th24_i} mm", pct_3),
        )
    return {"rows": rows}
