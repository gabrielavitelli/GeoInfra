"""Ops Raijin combined LOS + rain chart."""
from __future__ import annotations

from monitor_web.services.ops_raijin_charts import (
    _iso_to_br,
    build_combined_los_rain,
    build_critical_los_series,
)


def test_iso_to_br():
    assert _iso_to_br("2026-11-16") == "16/11/2026"
    assert _iso_to_br("2026-10-04T12:00:00Z") == "04/10/2026"
    assert _iso_to_br("2026-11-16", short=True) == "16/11"


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
    # Median across stations, not sum / not inflated mean.
    assert out["rain"]["y"] == [20.0, 30.0]
    assert out["points"][0]["x_br"] == ["01/09/2026", "01/10/2026"]
    assert "mm/mês" in out["axes"]["y_rain"]


def test_combined_rain_drops_absurd_monthly():
    precip = {
        "available": True,
        "months": ["2026-03"],
        "series": [
            {"id": "ok", "y": [120.0]},
            {"id": "bad", "y": [9000.0]},
            {"id": "ok2", "y": [180.0]},
        ],
    }
    hyp3 = {"available": False, "timeseries": {"x": [], "y": []}}
    out = build_combined_los_rain(precip, hyp3, lang_pt=True)
    assert out["available"] is True
    assert out["rain"]["y"] == [150.0]  # median of 120,180 (9000 dropped)


def test_combined_empty_when_both_missing():
    out = build_combined_los_rain(
        {"available": False, "months": [], "series": []},
        {"available": False, "timeseries": {"x": [], "y": []}, "note": "waiting"},
        lang_pt=True,
    )
    assert out["available"] is False
    assert out["note"] == "waiting"


def test_combined_critical_points_and_daily_rain():
    rain = {
        "available": True,
        "x_iso": ["2026-11-10", "2026-11-11"],
        "x_br": ["10/11/2026", "11/11/2026"],
        "daily_mm": [2.0, 5.0],
        "month_accum_mm": [2.0, 7.0],
        "label_daily": "Chuva diária",
        "label_accum": "Acumulado mensal",
        "unit_daily": "mm/d",
        "unit_accum": "mm/mês",
    }
    los = {
        "available": True,
        "points": [
            {
                "id": "km375",
                "label": "Talude Zentra km 375",
                "color": "#fb7185",
                "monitored": True,
                "x_iso": ["2026-11-04", "2026-11-16"],
                "x_br": ["04/11/2026", "16/11/2026"],
                "y": [0.0, -12.5],
            },
            {
                "id": "km373",
                "label": "Corte km 373 (túnel)",
                "color": "#38bdf8",
                "monitored": True,
                "x_iso": ["2026-11-04", "2026-11-16"],
                "x_br": ["04/11/2026", "16/11/2026"],
                "y": [0.0, -8.0],
            },
        ],
        "band": {
            "x_iso": ["2026-11-04", "2026-11-16"],
            "x_br": ["04/11/2026", "16/11/2026"],
            "ymin": [0.0, -12.5],
            "ymax": [0.0, -8.0],
        },
        "unit_label": "LOS acum. (mm)",
    }
    out = build_combined_los_rain(rain=rain, los=los, lang_pt=True)
    assert out["available"] is True
    assert len(out["points"]) == 2
    assert out["band"]["ymin"] == [0.0, -12.5]
    assert out["rain_daily"]["unit"] == "mm/d"
    assert out["rain_accum"]["unit"] == "mm/mês"
    assert "mm/d" in out["axes"]["y_rain"]
    assert "mm/mês" in out["axes"]["y_rain"]
    assert out["axes"]["y_los"] == "LOS acum. (mm)"


def test_critical_los_qc_rejects_long_baseline_fringes(monkeypatch):
    """Short-baseline QC must drop multi-fringe / long-Δt jobs."""
    rows = [
        # Bad: ~24 fringe-scale jump, long baseline
        {"acq_date": "2026-11-04", "ref_date": "2026-09-01", "km": 373.0, "los_mm": -620.0, "job_id": "bad"},
        {"acq_date": "2026-11-04", "ref_date": "2026-09-01", "km": 375.0, "los_mm": -640.0, "job_id": "bad"},
        # Good: 12-day pair, small LOS
        {"acq_date": "2026-11-04", "ref_date": "2026-10-23", "km": 373.0, "los_mm": -4.2, "job_id": "good1"},
        {"acq_date": "2026-11-04", "ref_date": "2026-10-23", "km": 375.0, "los_mm": -5.1, "job_id": "good1"},
        {"acq_date": "2026-11-16", "ref_date": "2026-11-04", "km": 373.0, "los_mm": -3.0, "job_id": "good2"},
        {"acq_date": "2026-11-16", "ref_date": "2026-11-04", "km": 375.0, "los_mm": -2.5, "job_id": "good2"},
    ]
    monkeypatch.setattr(
        "monitor_web.services.ops_raijin_charts._load_hyp3_rows",
        lambda **_kw: rows,
    )
    out = build_critical_los_series(days=60, lang_pt=True)
    assert out["available"] is True
    by_id = {p["id"]: p for p in out["points"]}
    assert "km373" in by_id
    assert "km375" in by_id
    # Cumulative stays in small-mm range (not hundreds).
    assert all(abs(v) < 50 for v in by_id["km373"]["y"])
    assert by_id["km373"]["x_br"] == ["04/11", "16/11"]
    # Band spans min/max of critical points at each date.
    assert len(out["band"]["ymin"]) == len(out["band"]["ymax"]) == 2
    assert out["band"]["ymin"][0] <= out["band"]["ymax"][0]
