"""Unit tests for Zentra sensor helpers (no BQ)."""
from __future__ import annotations

from monitor_web.services.zentra_sensors import (
    _fmt_value,
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
                    "label": "VWC solo",
                    "value_txt": "0.218",
                    "unit": "m³/m³",
                }
            ],
        }
    )
    assert "Zentra z6-35939" in html
    assert "VWC solo" in html
    assert "km 412" in html
    assert "TEROS 12" in html
