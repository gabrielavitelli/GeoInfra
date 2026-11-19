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
    assert "Ver séries" in html
    assert "efc-zentra-open-series" in html
    assert "zentra_z6_35939" in html


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


def test_enrich_sensor_rows_mean_vwc_across_ports(tmp_path: Path, monkeypatch):
    from monitor_web.services.zentra_sensors import enrich_sensor_rows_with_port_averages

    readings = tmp_path / "readings.ndjson"
    lines = []
    for port, val in ((1, 0.20), (2, 0.30), (3, 0.10)):
        lines.append(
            json.dumps(
                {
                    "location_id": "zentra_z6_35939",
                    "port_num": port,
                    "sensor_name": "TEROS 12",
                    "measurement": "Water Content",
                    "value": val,
                    "datetime": "2026-11-18T12:00:00-03:00",
                }
            )
        )
    readings.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("ZENTRA_READINGS_GCS_URI", str(readings))
    monkeypatch.delenv("ZENTRA_SKIP_GCS_FALLBACK", raising=False)

    rows = [
        {
            "location_id": "zentra_z6_35939",
            "name": "z6-35939",
            "primary": None,
            "port_children": [],
            "avg_port_count": 0,
        }
    ]
    enrich_sensor_rows_with_port_averages(rows, lang_pt=True)
    assert rows[0]["avg_port_count"] == 3
    assert rows[0]["primary"]["is_average"] is True
    assert abs(rows[0]["primary"]["value"] - 0.2) < 1e-9
    assert rows[0]["primary"]["label"] == "VWC médio"
    assert len(rows[0]["port_children"]) == 3


def test_build_taludes_monitorados_clusters_nearby():
    from monitor_web.services.zentra_sensors import build_taludes_monitorados

    rows = [
        {
            "location_id": "zentra_a",
            "name": "a",
            "lat": -4.2600,
            "lon": -46.5343,
            "km": 374.0,
        },
        {
            "location_id": "zentra_b",
            "name": "b",
            "lat": -4.2601,
            "lon": -46.5344,
            "km": 374.1,
        },
        {
            "location_id": "zentra_z6_35950",
            "name": "z6-35950",
            "lat": -23.5545,
            "lon": -46.73128,
            "km": None,
        },
    ]
    sites = build_taludes_monitorados(rows, lang_pt=True, main_line=None, cum_km=None)
    # Two geographic clusters (corridor pair + Almeida/SP).
    assert len(sites) == 2
    labels = [s["label"] for s in sites]
    assert any("Talude km 374" in lab for lab in labels)
    assert any("Almeida" in lab for lab in labels)
    assert any(s["n_sensors"] == 2 for s in sites)
    assert any(s["n_sensors"] == 1 and s.get("km") is None for s in sites)


def test_nearest_km_rejects_far_off_corridor():
    from monitor_web.services.zentra_sensors import _nearest_km

    # Tiny fake rail near Zentra corridor.
    main_line = [[-4.26, -46.53], [-4.27, -46.54]]
    cum_km = [374.0, 375.0]
    assert _nearest_km(-4.2601, -46.534, main_line, cum_km) is not None
    # São Paulo Almeida — must not invent EFC km ~862.
    assert _nearest_km(-23.5545, -46.73128, main_line, cum_km) is None


def test_cluster_sensor_rows_into_sites_mean_vwc():
    from monitor_web.services.zentra_sensors import cluster_sensor_rows_into_sites

    # Eight loggers near official Vale km ~375 (~tens of metres apart) → one site card.
    base_lat, base_lon = -4.25998, -46.53486
    rows = []
    for i in range(8):
        rows.append(
            {
                "location_id": f"zentra_z6_{35930 + i}",
                "name": f"z6-{35930 + i}",
                "lat": base_lat + i * 0.00005,  # ~5.5 m steps
                "lon": base_lon + i * 0.00005,
                "km": 375.0 + i * 0.01,
                "status": "active",
                "status_label": "ATIVO",
                "sensors": ["TEROS 12"],
                "primary": {
                    "variable": "soil_vwc",
                    "label": "VWC médio",
                    "value": 0.20 + i * 0.01,
                    "value_txt": f"{0.20 + i * 0.01:.3f}",
                    "unit": "m³/m³",
                    "is_average": True,
                    "n_ports": 3,
                },
                "port_children": [
                    {"label": f"Porta {p}", "var_label": "VWC", "value_txt": "0.2", "unit": "m³/m³", "port": p}
                    for p in (1, 2, 3)
                ],
                "avg_port_count": 3,
                "freshness": "hoje",
                "sinal": "90%",
                "asset_class": "geotech",
                "location_line": "Trecho km 375",
            }
        )
    # Far SP logger stays its own card.
    rows.append(
        {
            "location_id": "zentra_far",
            "name": "far",
            "lat": -23.55,
            "lon": -46.73,
            "km": None,
            "status": "active",
            "status_label": "ATIVO",
            "sensors": ["ATMOS 41"],
            "primary": {
                "variable": "soil_vwc",
                "label": "VWC",
                "value": 0.15,
                "value_txt": "0.150",
                "unit": "m³/m³",
            },
            "port_children": [],
            "avg_port_count": 0,
            "freshness": "hoje",
            "sinal": "—",
            "asset_class": "meteo",
            "location_line": "SP",
        }
    )
    sites = cluster_sensor_rows_into_sites(rows, lang_pt=True, cluster_m=120.0)
    assert len(sites) == 2
    big = next(s for s in sites if s["n_loggers"] == 8)
    assert "Talude km 375" in big["name"]
    assert big["primary"]["is_average"] is True
    assert abs(big["primary"]["value"] - (sum(0.20 + i * 0.01 for i in range(8)) / 8)) < 1e-9
    assert len(big["loggers"]) == 8
    assert all(len(lg["port_children"]) == 3 for lg in big["loggers"])


def test_popup_vars_include_all_env():
    from monitor_web.services.zentra_sensors import POPUP_VARS

    needed = {
        "soil_vwc",
        "water_potential_kpa",
        "precip_mm",
        "soil_temp_c",
        "temp_c",
        "rh_pct",
        "pressure_hpa",
        "battery_pct",
        "signal_pct",
        "battery_v",
    }
    assert needed.issubset(set(POPUP_VARS))


def test_build_tuneis_monitorados_from_ranges(monkeypatch):
    from monitor_web.services import zentra_sensors as zs
    from monitor_web.services.map_3d_overlay import _TUNNEL_FOCUS, _TUNNEL_KM_RANGES

    # Official estaca focus coords — no rail / lib_efc required.
    sites = zs.build_tuneis_monitorados(lang_pt=True, main_line=None, cum_km=None)
    assert len(sites) == len(_TUNNEL_FOCUS) == len(_TUNNEL_KM_RANGES)
    assert all(s["kind"] == "tunnel" for s in sites)
    assert any("Corte" in s["label"] for s in sites)
    # Second cut near Zentra (~374.6 / estaca 374+600).
    near = next(s for s in sites if abs(float(s["km"]) - 374.6) < 0.2)
    assert abs(float(near["lat"]) - (-4.257259)) < 1e-5
    assert abs(float(near["lon"]) - (-46.533335)) < 1e-5
