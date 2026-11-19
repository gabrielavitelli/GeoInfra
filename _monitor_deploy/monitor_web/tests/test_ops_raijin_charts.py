"""Ops Raijin combined LOS + rain chart."""
from __future__ import annotations

from monitor_web.services.ops_raijin_charts import build_combined_los_rain


def test_combined_los_rain_mean_and_single_title():
    precip = {
        "available": True,
        "months": ["2026-09", "2026-10"],
        "series": [
            {"id": "a", "y": [10.0, 20.0]},
            {"id": "b", "y": [30.0, 40.0]},
        ],
    }
    hyp3 = {
        "available": True,
        "timeseries": {
            "x": ["2026-09-01", "2026-10-01"],
            "y": [1.5, -0.8],
            "label": "LOS médio km 370–380",
        },
        "note": "6 datas · 9 bins km · fonte bq:efc_clima.hyp3_los_km",
    }
    out = build_combined_los_rain(precip, hyp3, lang_pt=True)
    assert out["available"] is True
    assert out["note"] is None
    assert out["title"].startswith("Deslocamento LOS (HyP3)")
    assert out["los"]["y"] == [1.5, -0.8]
    # Mean across stations, not sum.
    assert out["rain"]["y"] == [20.0, 30.0]


def test_combined_empty_when_both_missing():
    out = build_combined_los_rain(
        {"available": False, "months": [], "series": []},
        {"available": False, "timeseries": {"x": [], "y": []}, "note": "waiting"},
        lang_pt=True,
    )
    assert out["available"] is False
    assert out["note"] == "waiting"
