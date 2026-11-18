"""Unit tests for sensor series helpers (no BQ)."""
from __future__ import annotations

import json
from pathlib import Path

from monitor_web.services.sensor_series import (
    _measurement_to_variable,
    build_sensor_series_payload,
    default_variable_for_ports,
    families_from_sensors,
    normalize_family,
    ports_for_device,
)


def test_normalize_family():
    assert normalize_family("TEROS 12") == "TEROS 12"
    assert normalize_family("ATMOS 41 G2") == "ATMOS"
    assert normalize_family("TEROS 21") == "TEROS 21"


def test_ports_for_mixed_device():
    ports = ports_for_device(["ATMOS 41 G2", "TEROS 12", "TEROS 21"], 5)
    ids = [p["id"] for p in ports]
    assert ids[0] == "ATMOS"
    assert "TEROS 12" in ids
    assert "TEROS 21" in ids
    assert ids[-1] == "Logger"
    fam, var = default_variable_for_ports(ports)
    assert fam == "TEROS 12"
    assert var == "soil_vwc"


def test_families_from_sensors_logger_always():
    assert families_from_sensors([]) == ["Logger"]
    assert "Logger" in families_from_sensors(["TEROS 12"])


def test_measurement_mapping():
    assert _measurement_to_variable("Water Content", None) == "soil_vwc"
    assert _measurement_to_variable("Matric Potential", None) == "water_potential_kpa"
    assert _measurement_to_variable("foo", "precip_mm") == "precip_mm"


def test_payload_multi_overview_from_gcs(tmp_path: Path, monkeypatch):
    devices = {
        "devices": [
            {
                "device_id": "z6-35950",
                "name": "z6-35950",
                "can_read_data": True,
                "ports": [
                    {"sensor_short_name": "ATMOS 41"},
                    {"sensor_short_name": "TEROS 12"},
                    {"sensor_short_name": "TEROS 21"},
                ],
                "location": {"latitude": -23.55, "longitude": -46.73},
            }
        ]
    }
    devices_path = tmp_path / "devices.json"
    devices_path.write_text(json.dumps(devices), encoding="utf-8")
    series_lines = [
        json.dumps(
            {
                "date": "2026-11-18",
                "location_id": "zentra_z6_35950",
                "variable": "soil_vwc",
                "value": 0.21,
                "source": "zentra",
            }
        ),
        json.dumps(
            {
                "date": "2026-11-18",
                "location_id": "zentra_z6_35950",
                "variable": "water_potential_kpa",
                "value": -12.0,
                "source": "zentra",
            }
        ),
        json.dumps(
            {
                "date": "2026-11-18",
                "location_id": "zentra_z6_35950",
                "variable": "precip_mm",
                "value": 0.0,
                "source": "zentra",
            }
        ),
    ]
    series_path = tmp_path / "series.ndjson"
    series_path.write_text("\n".join(series_lines) + "\n", encoding="utf-8")
    monkeypatch.setattr("monitor_web.services.sensor_series._client", lambda: None)
    monkeypatch.setenv("ZENTRA_DEVICES_GCS_URI", str(devices_path))
    monkeypatch.setenv("ZENTRA_LOCATIONS_GCS_URI", str(tmp_path / "missing.json"))
    monkeypatch.setenv("ZENTRA_SERIES_GCS_URI", str(series_path))
    monkeypatch.setenv("ZENTRA_READINGS_GCS_URI", str(tmp_path / "missing_readings.ndjson"))
    monkeypatch.delenv("ZENTRA_SKIP_GCS_FALLBACK", raising=False)

    # Dense readings fixture — multiple raw timestamps (no daily mean).
    readings_lines = []
    for i, hour in enumerate((10, 11, 12, 13, 14)):
        readings_lines.append(
            json.dumps(
                {
                    "location_id": "zentra_z6_35950",
                    "measurement": "Water Content",
                    "mapped_variable": "soil_vwc",
                    "value": 0.20 + i * 0.01,
                    "datetime": f"2026-11-18T{hour:02d}:00:00+00:00",
                    "port_num": 1,
                }
            )
        )
        readings_lines.append(
            json.dumps(
                {
                    "location_id": "zentra_z6_35950",
                    "measurement": "Battery Percent",
                    "mapped_variable": "battery_pct",
                    "value": 90.0,
                    "datetime": f"2026-11-18T{hour:02d}:00:00+00:00",
                    "port_num": 0,
                }
            )
        )
        readings_lines.append(
            json.dumps(
                {
                    "location_id": "zentra_z6_35950",
                    "measurement": "Signal",
                    "mapped_variable": "signal_pct",
                    "value": 55.0,
                    "datetime": f"2026-11-18T{hour:02d}:00:00+00:00",
                    "port_num": 0,
                }
            )
        )
    readings_path = tmp_path / "readings.ndjson"
    readings_path.write_text("\n".join(readings_lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("ZENTRA_READINGS_GCS_URI", str(readings_path))

    payload = build_sensor_series_payload(
        location_id="zentra_z6_35950",
        family="__overview__",
        multi=True,
        days=7,
    )
    assert payload["available"] is True
    assert payload["mode"] == "gallery"
    assert payload["resolution"] == "raw"
    assert len(payload["traces"]) >= 2
    assert payload["port_chips"][0]["id"] == "__overview__"
    assert payload["n_points"] >= 5
    vwc = next(t for t in payload["traces"] if t["variable"] == "soil_vwc")
    assert vwc["n_points"] >= 5
    assert "battery_pct" in payload["active_variables"]
    assert "signal_pct" in payload["active_variables"]
