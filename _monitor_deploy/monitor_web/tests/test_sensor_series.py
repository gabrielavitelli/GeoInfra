"""Unit tests for sensor series helpers (no BQ)."""
from __future__ import annotations

from monitor_web.services.sensor_series import (
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
