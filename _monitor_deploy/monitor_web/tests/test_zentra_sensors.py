"""Unit tests for Zentra sensor helpers (no BQ)."""
from __future__ import annotations

import json
from pathlib import Path

from monitor_web.services.zentra_sensors import (
    _fmt_value,
    build_zentra_sensor_rows,
    load_zentra_devices_from_gcs,
    zentra_popup_html,
)


def test_fmt_value_filters_water_potential_sentinel():
    assert _fmt_value("water_potential_kpa", -99999.0) is None
    assert _fmt_value("water_potential_kpa", -12.5) == "-12.5"
    assert _fmt_value("soil_vwc", 0.218) == "0.218"
    assert _fmt_value("battery_pct", 89.0) == "89"


def test_popup_html_compact():
    html = zentra_popup_html(
        {
            "name": "z6-35939",
            "location_id": "zentra_z6_35939",
            "km": 412.0,
            "freshness": "ok",
            "sensors": ["TEROS 12", "TEROS 21"],
            "series": [
                {
                    "variable": "soil_vwc",
                    "label": "VWC: solo",
                    "value_txt": "0.218",
                    "unit": "m³/m³",
                }
            ],
        }
    )
    assert "Zentra z6-35939" in html
    assert "VWC: solo" in html
    assert "km 412" in html
    assert "TEROS 12" in html


def test_gcs_devices_fallback_from_raw_json(tmp_path: Path, monkeypatch):
    payload = {
        "pulled_at_utc": "2026-11-18T06:00:00+00:00",
        "devices": [
            {
                "device_id": "z6-35936",
                "name": "z6-35936",
                "model": "ZL6",
                "can_read_data": True,
                "data_range": {"last_measurement": 1794947400},
                "ports": [
                    {"sensor_short_name": "TEROS 12"},
                    {"sensor_short_name": "TEROS 12"},
                ],
                "location": {"latitude": -4.25998, "longitude": -46.53486},
            },
            {
                "device_id": "z6-no-coords",
                "name": "z6-no-coords",
                "can_read_data": True,
                "ports": [],
                "location": {"latitude": 0.0, "longitude": 0.0},
            },
            {
                "device_id": "z6-35950",
                "name": "z6-35950",
                "model": "ZL6",
                "can_read_data": True,
                "ports": [
                    {"sensor_short_name": "ATMOS 41"},
                    {"sensor_short_name": "TEROS 12"},
                ],
                "location": {"latitude": -23.5545, "longitude": -46.73128},
            },
        ],
    }
    path = tmp_path / "zentra_devices_latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.delenv("ZENTRA_SKIP_GCS_FALLBACK", raising=False)
    monkeypatch.setenv("ZENTRA_DEVICES_GCS_URI", str(path))
    monkeypatch.setenv("ZENTRA_LOCATIONS_GCS_URI", str(tmp_path / "missing_locations.json"))

    df = load_zentra_devices_from_gcs()
    assert df is not None
    assert len(df) == 2
    ids = set(df["location_id"].tolist())
    assert ids == {"zentra_z6_35936", "zentra_z6_35950"}
    mixed = df.loc[df["location_id"] == "zentra_z6_35950"].iloc[0]
    assert mixed["asset_class"] == "mixed"
    assert "TEROS 12" in list(mixed["sensors"])


def test_build_rows_uses_gcs_when_bq_client_missing(tmp_path: Path, monkeypatch):
    payload = {
        "devices": [
            {
                "device_id": "z6-35939",
                "name": "z6-35939",
                "can_read_data": True,
                "ports": [{"sensor_short_name": "TEROS 21"}],
                "location": {"latitude": -4.26, "longitude": -46.53432},
                "data_range": {"last_measurement": 1794947400},
            }
        ]
    }
    path = tmp_path / "devices.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "monitor_web.services.zentra_sensors._client",
        lambda: None,
    )
    monkeypatch.setattr(
        "monitor_web.services.zentra_sensors.load_zentra_latest_from_gcs",
        lambda **kwargs: {},
    )
    monkeypatch.setenv("ZENTRA_DEVICES_GCS_URI", str(path))
    monkeypatch.setenv("ZENTRA_LOCATIONS_GCS_URI", str(tmp_path / "nope.json"))

    rows = build_zentra_sensor_rows()
    assert len(rows) == 1
    assert rows[0]["location_id"] == "zentra_z6_35939"
    assert rows[0]["status"] == "active"
