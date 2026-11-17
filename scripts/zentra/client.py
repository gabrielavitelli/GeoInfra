"""Minimal ZENTRA Cloud v5 client (stdlib only).

Auth: ``X-API-Key`` header (Secret Manager ``zentra-api-token``).
Docs: https://api.zentracloud.io/openapi.json

Endpoints used:
  GET /v5/devices
  GET /v5/devices/{device_id}/data  (prefer since_reading_id cursors)
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, MutableMapping, Optional, Sequence

DEFAULT_BASE_URL = "https://api.zentracloud.io"
# OpenAPI expand tokens (response field for timestamps is still `data_range`).
DEFAULT_EXPAND = "ports,location,settings,connectivity,max_min_timestamp"


class ZentraAuthError(RuntimeError):
    """Missing/invalid API key."""


class ZentraApiError(RuntimeError):
    """Non-success HTTP response from ZENTRA."""

    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"ZENTRA HTTP {status} for {url}: {body[:300]}")


def _env_api_token() -> Optional[str]:
    for name in ("ZENTRA_API_TOKEN", "ZENTRA_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return None


def redact_secret(value: str, keep: int = 4) -> str:
    """Safe preview for logs (never print full secrets)."""
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}…{value[-keep:]} (len={len(value)})"


@dataclass
class ZentraClient:
    api_token: str
    base_url: str = DEFAULT_BASE_URL
    timeout_s: float = 90.0
    max_retries: int = 6
    min_request_interval_s: float = 1.0
    _last_request_at: float = field(default=0.0, repr=False)

    @classmethod
    def from_env(cls, **kwargs: Any) -> "ZentraClient":
        tok = _env_api_token()
        if not tok:
            raise ZentraAuthError(
                "Defina ZENTRA_API_TOKEN (Secret Manager zentra-api-token) no ambiente."
            )
        return cls(api_token=tok, **kwargs)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        params: MutableMapping[str, Any] = dict(query or {})
        flat: dict[str, str] = {}
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, (list, tuple)):
                flat[k] = ",".join(str(x) for x in v)
            else:
                flat[k] = str(v)
        qs = urllib.parse.urlencode(flat)
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if qs:
            url = f"{url}?{qs}"

        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_token,
        }

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            gap = time.monotonic() - self._last_request_at
            if gap < self.min_request_interval_s:
                time.sleep(self.min_request_interval_s - gap)
            req = urllib.request.Request(url, method=method.upper(), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    self._last_request_at = time.monotonic()
                    raw = resp.read().decode("utf-8", errors="replace")
                    if not raw.strip():
                        return {}
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                self._last_request_at = time.monotonic()
                body = e.read().decode("utf-8", errors="replace")
                if e.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    try:
                        wait_s = float(retry_after) if retry_after else min(8 * (2**attempt), 120)
                    except ValueError:
                        wait_s = min(8 * (2**attempt), 120)
                    print(
                        f"[zentra] HTTP {e.code} — wait {wait_s:.0f}s "
                        f"(attempt {attempt + 1})",
                        flush=True,
                    )
                    time.sleep(wait_s)
                    continue
                raise ZentraApiError(e.code, body, url) from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise
        raise RuntimeError(f"ZENTRA request failed after retries: {last_err}")

    def list_devices(
        self,
        *,
        expand: str = DEFAULT_EXPAND,
        limit: int = 100,
        organization_id: Optional[str] = None,
        units: str = "metric",
    ) -> list[dict[str, Any]]:
        """Return all devices across pages."""
        page_num = 1
        out: list[dict[str, Any]] = []
        while True:
            query: dict[str, Any] = {
                "expand": expand,
                "limit": limit,
                "page_num": page_num,
                "units": units,
            }
            if organization_id:
                query["organization_id"] = organization_id
            data = self._request("GET", "/v5/devices", query=query)
            if not isinstance(data, dict):
                raise ZentraApiError(200, f"expected object, got {type(data)}", "/v5/devices")
            batch = data.get("devices") or []
            if not isinstance(batch, list):
                raise ZentraApiError(200, "devices is not a list", "/v5/devices")
            out.extend(batch)
            pagination = data.get("pagination") or {}
            total_pages = int(pagination.get("total_pages") or 1)
            if page_num >= total_pages or not batch:
                break
            page_num += 1
        return out

    def fetch_device_data_page(
        self,
        device_id: str,
        *,
        since_reading_id: Optional[int] = None,
        window: Optional[str] = None,
        start_datetime: Optional[str] = None,
        end_datetime: Optional[str] = None,
        units: str = "metric",
        aggregate: str = "raw",
        next_token: Optional[str] = None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "units": units,
            "aggregate": aggregate,
        }
        if since_reading_id is not None:
            query["since_reading_id"] = int(since_reading_id)
        else:
            if window:
                query["window"] = window
            if start_datetime:
                query["start_datetime"] = start_datetime
            if end_datetime:
                query["end_datetime"] = end_datetime
            if next_token:
                query["next_token"] = next_token
        return self._request("GET", f"/v5/devices/{device_id}/data", query=query)

    def iter_device_data(
        self,
        device_id: str,
        *,
        since_reading_id: Optional[int] = None,
        window: Optional[str] = None,
        start_datetime: Optional[str] = None,
        end_datetime: Optional[str] = None,
        units: str = "metric",
        aggregate: str = "raw",
        max_pages: int = 200,
    ) -> Iterator[dict[str, Any]]:
        """Yield ValueEntity rows. Prefer ``since_reading_id`` to minimize pages.

        When using ``since_reading_id``, follow ``pagination.next_reading_id``
        until null / empty. Otherwise follow ``next_token``.
        """
        pages = 0
        cursor_since = since_reading_id
        next_token: Optional[str] = None
        while pages < max_pages:
            pages += 1
            if cursor_since is not None:
                data = self.fetch_device_data_page(
                    device_id,
                    since_reading_id=cursor_since,
                    units=units,
                    aggregate=aggregate,
                )
            else:
                data = self.fetch_device_data_page(
                    device_id,
                    window=window,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    units=units,
                    aggregate=aggregate,
                    next_token=next_token,
                )
            values = data.get("values") or []
            if isinstance(values, list):
                for row in values:
                    yield row
            pagination = data.get("pagination") or {}
            if cursor_since is not None:
                nxt = pagination.get("next_reading_id")
                if nxt is None or not values:
                    # Empty page or terminal — also advance cursor via max reading_id
                    break
                # Guard against stuck cursor
                if int(nxt) <= int(cursor_since):
                    break
                cursor_since = int(nxt)
            else:
                next_token = pagination.get("next_token")
                if not next_token or not values:
                    break

    def fetch_device_data(
        self,
        device_id: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return list(self.iter_device_data(device_id, **kwargs))


def device_status(device: Mapping[str, Any], *, stale_days: float = 14.0) -> str:
    """active | stale | empty — for cheap skip decisions."""
    ports = device.get("ports") or []
    loc = device.get("location") or {}
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if not ports and (lat in (None, 0, 0.0)) and (lon in (None, 0, 0.0)):
        return "empty"
    data_range = device.get("data_range") or {}
    last_ts = data_range.get("last_measurement")
    if last_ts is None:
        if not ports:
            return "empty"
        return "stale"
    age_days = (time.time() - float(last_ts)) / 86400.0
    if age_days > stale_days:
        return "stale"
    return "active"


def sort_devices_for_ingest(
    devices: Sequence[Mapping[str, Any]],
    *,
    priority_ids: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    """Priority cluster first, then active, then others. Skip order for logging."""
    priority = {d.lower() for d in (priority_ids or [])}
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for d in devices:
        did = str(d.get("device_id") or "")
        status = device_status(d)
        if did.lower() in priority:
            tier = 0 if status == "active" else 1
        elif status == "active":
            tier = 2
        elif status == "stale":
            tier = 3
        else:
            tier = 4
        ranked.append((tier, did, dict(d)))
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [d for _, _, d in ranked]
