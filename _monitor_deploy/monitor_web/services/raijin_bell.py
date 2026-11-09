"""Painel do sino — últimos alertas Raijin + excedências de limiar EWS/MERGE."""
from __future__ import annotations

from monitor_web.i18n import get_lang
from monitor_web.services.cache_keys import bq_fingerprint, cached_call
from monitor_web.services.home_fragments import _active_dataset


def _merge_alerts(raijin: list, thresholds: list, *, limit: int = 40) -> list:
    """Une Raijin (TP/FN) e limiares; ordena por dia desc, prioriza ews/limiar no mesmo dia."""
    merged = []
    for a in raijin or []:
        item = dict(a)
        item.setdefault("kind", "raijin")
        item.setdefault("source", "raijin")
        merged.append(item)
    for a in thresholds or []:
        item = dict(a)
        item.setdefault("kind", "threshold")
        merged.append(item)

    def _key(a: dict):
        day_n = int(str(a.get("day") or "0").replace("-", "") or 0)
        kind_rank = 0 if a.get("kind") == "threshold" else 1
        oc = str(a.get("outcome") or "")
        oc_rank = {"ews": 0, "limiar": 1, "tp": 2, "fn": 3, "fp": 4}.get(oc, 5)
        # day DESC, then threshold before raijin, then outcome priority, then mm DESC
        return (-day_n, kind_rank, oc_rank, -float(a.get("mm") or 0), int(a.get("km") or 0))

    merged.sort(key=_key)
    # Dedup (day, km, outcome)
    seen: set[tuple] = set()
    out: list = []
    for a in merged:
        key = (str(a.get("day") or ""), int(a.get("km") or 0), str(a.get("outcome") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= limit:
            break
    return out


def build_raijin_bell_payload(request) -> dict:
    lang_pt = get_lang(request) == "pt"
    dataset = _active_dataset()
    days = 30
    try:
        raw_days = int((request.GET.get("days") or "30").strip())
        days = max(7, min(raw_days, 120))
    except ValueError:
        days = 30

    fp = bq_fingerprint()

    def _load_raijin() -> list:
        from gcp.bq_raijin import get_raijin_recent_alerts

        return get_raijin_recent_alerts(
            dataset=dataset, days=days, limit=40, outcomes=("tp", "fn")
        )

    def _load_thresholds() -> list:
        from monitor_web.services.threshold_bell import get_recent_threshold_alerts

        return get_recent_threshold_alerts(dataset=dataset, days=days, limit=40)

    def _load_status() -> dict:
        from monitor_web.services.threshold_bell import threshold_status_snapshot

        return threshold_status_snapshot(dataset=dataset)

    raijin = cached_call(f"raijin_bell_v3_tpfn_{dataset}_{days}_{fp}", 300, _load_raijin) or []
    thresholds = cached_call(f"thresh_bell_v1_{dataset}_{days}_{fp}", 300, _load_thresholds) or []
    status = cached_call(f"thresh_status_v1_{dataset}_{fp}", 300, _load_status) or {}
    alerts = _merge_alerts(raijin, thresholds, limit=40)

    latest_day = max((a.get("day") or "") for a in alerts) if alerts else None
    n_latest = sum(1 for a in alerts if a.get("day") == latest_day) if latest_day else 0
    n_thresh = sum(1 for a in alerts if a.get("kind") == "threshold")

    return {
        "available": True,
        "lang_pt": lang_pt,
        "days": days,
        "count": len(alerts),
        "n_today": n_latest,
        "n_threshold": n_thresh,
        "latest_day": latest_day,
        "has_unread": n_latest > 0,
        "alerts": alerts,
        "ews_status": status,
        "title": (
            "Últimos alertas (Raijin · limiares EWS)"
            if lang_pt
            else "Latest alerts (Raijin · EWS thresholds)"
        ),
        "empty": (
            "Nenhum alerta Raijin nem ultrapassagem de limiar EWS no período."
            if lang_pt
            else "No Raijin alerts or EWS threshold exceedances in the period."
        ),
    }
