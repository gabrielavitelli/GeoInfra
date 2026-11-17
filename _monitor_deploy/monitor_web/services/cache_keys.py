from __future__ import annotations

import hashlib
import json
from typing import Any

from django.core.cache import cache


def cache_key_digest(value: Any) -> str:
    """Hash curto e estável para partes grandes de chaves de cache (ex.: marcadores do mapa)."""
    if value is None:
        blob = ""
    elif isinstance(value, (tuple, list, dict, set)):
        blob = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    else:
        blob = str(value)
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:16]


def map_folium_cache_key(
    *,
    ver: str,
    sel: str,
    dataset: str,
    accum_hours: int,
    km_step: int,
    cache_tag: str,
    alert_mk: tuple,
    csv_mtimes: tuple[float | int, ...],
    merge_period: str = "",
    fp: str | None = None,
) -> str:
    """Chave compacta para HTML Folium (evita CacheKeyWarning / limite memcached)."""
    fp_val = fp if fp is not None else bq_fingerprint()
    mt = "_".join(str(t) for t in csv_mtimes)
    mp = cache_key_digest(merge_period) if merge_period else "0"
    return (
        f"map_{ver}_{sel}_{dataset}_{accum_hours}_{km_step}_{cache_tag}_"
        f"{cache_key_digest(fp_val)}_{cache_key_digest(alert_mk)}_{mp}_{mt}"
    )


def map_folium_stable_cache_key(
    *,
    ver: str,
    sel: str,
    dataset: str,
    accum_hours: int,
    km_step: int,
    cache_tag: str,
    fp: str | None = None,
) -> str:
    """Chave estável (sem alert_mk) para peek rápido antes do BQ day_exceed."""
    fp_val = fp if fp is not None else bq_fingerprint()
    return (
        f"map_{ver}_{sel}_{dataset}_{accum_hours}_{km_step}_{cache_tag}_"
        f"stable_{cache_key_digest(fp_val)}"
    )


def cached_call(key: str, ttl: int, fn, *args, **kwargs):
    hit = cache.get(key)
    if hit is not None:
        return hit
    val = fn(*args, **kwargs)
    cache.set(key, val, ttl)
    return val


def cached_peek(key: str):
    return cache.get(key)


def cached_call_swr(key: str, ttl: int, fn, *args, stale_ttl: int | None = None, **kwargs):
    """Cache com stale-while-revalidate barato (sem thread extra no hit fresco).

    - Hit fresco (< ttl): devolve imediatamente.
    - Hit stale (< stale_ttl): devolve valor antigo e revalida em background.
    - Miss: calcula sincronamente.
    """
    import logging
    import threading
    import time

    log = logging.getLogger(__name__)
    soft = max(1, int(ttl))
    hard = max(soft, int(stale_ttl if stale_ttl is not None else soft * 4))
    meta_key = f"{key}__swr_meta"
    hit = cache.get(key)
    meta = cache.get(meta_key) or {}
    now = time.time()
    stored_at = float(meta.get("t") or 0.0)

    if hit is not None and stored_at and (now - stored_at) < soft:
        return hit

    def _store(val):
        cache.set(key, val, hard)
        cache.set(meta_key, {"t": time.time()}, hard)
        return val

    if hit is not None and stored_at and (now - stored_at) < hard:
        # Stale: serve already-cached value and refresh off the request path.
        def _refresh():
            try:
                _store(fn(*args, **kwargs))
            except Exception as exc:
                log.warning("cached_call_swr refresh failed key=%s: %s", key, exc)

        try:
            threading.Thread(target=_refresh, name=f"swr:{key[:40]}", daemon=True).start()
        except Exception:
            pass
        return hit

    return _store(fn(*args, **kwargs))


def bq_fingerprint() -> str:
    try:
        from gcp.monitor_bq_context import monitor_bq_cache_fingerprint

        return monitor_bq_cache_fingerprint()
    except Exception:
        return "0"
