"""Unit tests for ZENTRA → clima_series mapping (no network)."""

from __future__ import annotations

import unittest

from scripts.zentra.client import device_status, sort_devices_for_ingest
from scripts.zentra.mapping import (
    classify_measurement,
    clima_series_payload,
    device_asset_class,
    location_id_from_device,
    reading_to_clima_row,
)


class MappingTests(unittest.TestCase):
    def test_precip(self):
        hit = classify_measurement("ATMOS 41 G2", "Precipitation", "mm")
        assert hit is not None
        self.assertEqual(hit["variable"], "precip_mm")
        self.assertEqual(hit["family"], "meteo")

    def test_vwc(self):
        hit = classify_measurement("TEROS 12", "Water Content", "m³/m³")
        assert hit is not None
        self.assertEqual(hit["variable"], "soil_vwc")
        self.assertEqual(hit["family"], "geotech")

    def test_matric_potential(self):
        hit = classify_measurement("TEROS 21", "Matric Potential", "kPa")
        assert hit is not None
        self.assertEqual(hit["variable"], "water_potential_kpa")

    def test_battery_scale(self):
        device = {
            "device_id": "z6-35938",
            "ports": [{"sensor_short_name": "ATMOS 41 G2"}],
            "location": {"latitude": -4.26, "longitude": -46.53},
        }
        row = reading_to_clima_row(
            device=device,
            value_row={
                "sensor_name": "Battery",
                "measurement": "Battery Voltage",
                "unit": "mV",
                "value": 3700,
                "timestamp": 1794938400,
                "datetime": "2026-11-17T18:00:00Z",
                "error_code": 0,
                "reading_id": 10,
                "port_num": None,
            },
        )
        assert row is not None
        self.assertEqual(row["variable"], "battery_v")
        self.assertAlmostEqual(row["value"], 3.7)
        self.assertEqual(row["location_id"], "zentra_z6_35938")
        self.assertEqual(row["date"], "2026-11-17")
        payload = clima_series_payload([row])[0]
        self.assertEqual(set(payload), {"date", "location_id", "source", "ingested_at", "variable", "value"})

    def test_asset_class_mixed(self):
        device = {
            "device_id": "z6-35938",
            "ports": [
                {"sensor_short_name": "ATMOS 41 G2"},
                {"sensor_short_name": "TEROS 12"},
            ],
        }
        self.assertEqual(device_asset_class(device), "mixed")

    def test_location_id(self):
        self.assertEqual(location_id_from_device({"device_id": "z6-35936"}), "zentra_z6_35936")

    def test_empty_status(self):
        d = {"device_id": "z6-35947", "ports": [], "location": {"latitude": 0.0, "longitude": 0.0}}
        self.assertEqual(device_status(d), "empty")

    def test_priority_sort(self):
        devices = [
            {"device_id": "z6-35950", "ports": [{"sensor_short_name": "ATMOS 41"}], "location": {"latitude": -23.5, "longitude": -46.7}, "data_range": {"last_measurement": 9e12}},
            {"device_id": "z6-35947", "ports": [], "location": {"latitude": 0, "longitude": 0}},
            {"device_id": "z6-35938", "ports": [{"sensor_short_name": "TEROS 12"}], "location": {"latitude": -4.26, "longitude": -46.53}, "data_range": {"last_measurement": 9e12}},
        ]
        ordered = sort_devices_for_ingest(devices, priority_ids=["z6-35938", "z6-35947"])
        self.assertEqual(ordered[0]["device_id"], "z6-35938")


if __name__ == "__main__":
    unittest.main()
