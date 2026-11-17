"""Minimal Orion Data API v1.3 client (stdlib only).

Auth model (official docs):
  1. API key from Account Settings (aka "User Key" in the portal UI)
  2. GET /token?apiKey=...  →  JWT valid ~7 days
  3. Authorization: Bearer <jwt> on every other call

Login + password are for the web UI only — not used by the API.
Never log or commit the API key / JWT.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, MutableMapping, Optional, Sequence

DEFAULT_BASE_URL = "https://api.oriondata.io/api"
DEFAULT_VERSION = "1.3"
PAGE_SIZE = 100  # Orion pagination: each offset page has up to 100 rows


class OrionAuthError(RuntimeError):
    """Missing/invalid API key or bearer token."""


class OrionApiError(RuntimeError):
    """Non-success HTTP response from Orion."""

    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"Orion HTTP {status} for {url}: {body[:300]}")


def _env_api_key() -> Optional[str]:
    # Prefer ORION_USER_KEY (portal wording); also accept ORION_API_KEY.
    for name in ("ORION_USER_KEY", "ORION_API_KEY"):
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
class OrionClient:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    version: str = DEFAULT_VERSION
    timeout_s: float = 60.0
    max_retries: int = 8
    min_request_interval_s: float = 0.35
    _token: Optional[str] = None
    _last_request_at: float = 0.0

    @classmethod
    def from_env(cls, **kwargs: Any) -> "OrionClient":
        key = _env_api_key()
        if not key:
            raise OrionAuthError(
                "Defina ORION_USER_KEY (ou ORION_API_KEY) no ambiente / Secret Manager. "
                "Não use login/senha na API."
            )
        return cls(api_key=key, **kwargs)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        auth: bool = True,
        accept_empty: bool = False,
    ) -> Any:
        params: MutableMapping[str, Any] = dict(query or {})
        # Flatten list params as comma-separated (Orion explode=false).
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

        headers = {"Accept": "application/json"}
        if auth:
            if not self._token:
                self.refresh_token()
            headers["Authorization"] = f"Bearer {self._token}"

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            # Gentle pacing to reduce Orion rate-limit 429s.
            gap = time.monotonic() - self._last_request_at
            if gap < self.min_request_interval_s:
                time.sleep(self.min_request_interval_s - gap)
            req = urllib.request.Request(url, method=method.upper(), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    self._last_request_at = time.monotonic()
                    raw = resp.read().decode("utf-8", errors="replace")
                    if not raw.strip():
                        if accept_empty:
                            return [] if path.rstrip("/").endswith(("sensorData", "validatedSensorData", "userDevices")) else {}
                        return None
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                self._last_request_at = time.monotonic()
                body = e.read().decode("utf-8", errors="replace")
                if e.code == 401 and auth:
                    # Token expired / rejected — refresh once then retry.
                    self._token = None
                    if attempt == 0:
                        self.refresh_token()
                        headers["Authorization"] = f"Bearer {self._token}"
                        continue
                if e.code == 429 and attempt < self.max_retries:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    try:
                        wait_s = float(retry_after) if retry_after else min(5 * (2 ** attempt), 120)
                    except ValueError:
                        wait_s = min(5 * (2 ** attempt), 120)
                    print(f"[orion] 429 rate limit — wait {wait_s:.0f}s (attempt {attempt + 1})", flush=True)
                    time.sleep(wait_s)
                    continue
                raise OrionApiError(e.code, body, url) from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 20))
                    continue
                raise
        raise RuntimeError(f"Orion request failed after retries: {last_err}")

    def refresh_token(self) -> str:
        """Exchange API key for JWT. Key goes as query param (Orion contract)."""
        data = self._request(
            "GET",
            "/token",
            query={"apiKey": self.api_key},
            auth=False,
        )
        if not isinstance(data, dict) or not data.get("token"):
            raise OrionAuthError(
                f"Resposta /token inesperada (chave {redact_secret(self.api_key)})."
            )
        self._token = str(data["token"])
        return self._token

    def list_devices(
        self, serial_numbers: Optional[Sequence[str]] = None
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if serial_numbers:
            query["serialNumbers"] = list(serial_numbers)
        data = self._request("GET", "/userDevices", query=query or None)
        if data is None:
            return []
        if not isinstance(data, list):
            raise OrionApiError(200, f"expected list, got {type(data)}", "/userDevices")
        return data

    def iter_sensor_data(
        self,
        *,
        start_date: str,
        end_date: str,
        sensor_ids: Optional[Sequence[int]] = None,
        soft_sensor_ids: Optional[Sequence[int]] = None,
        validated: bool = False,
    ) -> Iterator[dict[str, Any]]:
        if not sensor_ids and not soft_sensor_ids:
            raise ValueError("Informe sensor_ids e/ou soft_sensor_ids")
        path = "/validatedSensorData" if validated else "/sensorData"
        offset = 0
        while True:
            query: dict[str, Any] = {
                "startDate": start_date,
                "endDate": end_date,
                "offset": offset,
                "version": self.version,
            }
            if sensor_ids:
                query["sensorIds"] = list(sensor_ids)
            if soft_sensor_ids:
                query["softSensorIds"] = list(soft_sensor_ids)
            page = self._request("GET", path, query=query, accept_empty=True)
            if not page:
                break
            if not isinstance(page, list):
                raise OrionApiError(200, f"expected list, got {type(page)}", path)
            for row in page:
                yield row
            if len(page) < PAGE_SIZE:
                break
            offset += 1

    def fetch_sensor_data(
        self,
        *,
        start_date: str,
        end_date: str,
        sensor_ids: Optional[Sequence[int]] = None,
        soft_sensor_ids: Optional[Sequence[int]] = None,
        validated: bool = False,
    ) -> list[dict[str, Any]]:
        return list(
            self.iter_sensor_data(
                start_date=start_date,
                end_date=end_date,
                sensor_ids=sensor_ids,
                soft_sensor_ids=soft_sensor_ids,
                validated=validated,
            )
        )


def flatten_device_sensors(devices: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per raw/soft sensor with parent device metadata."""
    rows: list[dict[str, Any]] = []
    for d in devices:
        base = {
            "device_id": d.get("deviceId"),
            "device_name": d.get("deviceName"),
            "reference": d.get("reference"),
            "serial_number": d.get("serialNumber"),
            "date_installed": d.get("dateInstalled"),
            "status": d.get("status"),
            "last_upload": d.get("lastUpload"),
            "latitude": d.get("latitude"),
            "longitude": d.get("longitude"),
            "battery_pct": d.get("batteryPercentage"),
        }
        for s in d.get("sensors") or []:
            rows.append(
                {
                    **base,
                    "kind": "raw",
                    "sensor_id": s.get("sensorId"),
                    "soft_sensor_id": None,
                    "sensor_type": s.get("sensorType"),
                    "uom": s.get("uom"),
                    "channel_number": s.get("channelNumber"),
                    "custom_name": s.get("customName"),
                    "scada_tag": s.get("scadaTag"),
                }
            )
        for s in d.get("softSensors") or []:
            rows.append(
                {
                    **base,
                    "kind": "soft",
                    "sensor_id": None,
                    "soft_sensor_id": s.get("softSensorId"),
                    "sensor_type": s.get("sensorType"),
                    "uom": s.get("uom"),
                    "channel_number": None,
                    "custom_name": s.get("customName"),
                    "scada_tag": s.get("scadaTag"),
                }
            )
    return rows
