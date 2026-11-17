from datetime import datetime, timezone

from scripts.orion_ews.ingest import cmd_catalog
from scripts.orion_ews.map_assets import (
    COLOR_GEOTECH,
    COLOR_METEO,
    COLOR_MIXED,
    build_map_catalog,
    device_to_map_asset,
    popup_html,
)
from scripts.orion_ews.mapping import (
    classify_sensor,
    device_asset_class,
    map_sensor_type,
)
from scripts.orion_ews.siting import siting_report


def test_classify_rain_vs_piezo_vs_inclino():
    assert classify_sensor("Rainfall", "mm")["family"] == "meteo"
    assert classify_sensor("Piezometer", "kPa")["variable"] == "piezo_kpa"
    assert classify_sensor("Inclinometer", "deg")["family"] == "geotech"
    assert map_sensor_type("Battery Voltage", "V") == "battery_v"


def test_mixed_logger_not_rain_only():
    mixed = {
        "deviceName": "EWS6536 - Abrigo km 149",
        "sensors": [
            {"sensorType": "Rainfall", "uom": "mm"},
            {"sensorType": "Water Level", "uom": "m", "channelNumber": 1},
        ],
    }
    assert device_asset_class(mixed) == "mixed"
    geo = {
        "deviceName": "EWS8801 - Piezo km 380",
        "sensors": [{"sensorType": "Piezometer", "uom": "kPa"}],
    }
    assert device_asset_class(geo) == "geotech"


def test_map_asset_ports_and_popup():
    device = {
        "deviceId": 8801,
        "deviceName": "EWS8801 - Piezo km 380",
        "serialNumber": "x",
        "status": "Online",
        "lastUpload": "2026-11-16T12:00:00Z",
        "latitude": -4.2,
        "longitude": -49.4,
        "batteryPercentage": 71,
        "sensors": [
            {"sensorId": 1, "sensorType": "Piezometer", "uom": "kPa", "channelNumber": 1, "customName": "PZ-01"},
            {"sensorId": 2, "sensorType": "Inclinometer", "uom": "deg", "channelNumber": 2, "customName": "INC-A"},
        ],
        "softSensors": [],
    }
    a = device_to_map_asset(device, now=datetime(2026, 11, 16, 14, tzinfo=timezone.utc))
    assert a["asset_class"] == "geotech"
    assert a["color"] == COLOR_GEOTECH
    assert a["km"] == 380
    assert a["layer"] == "geotech"
    ports = {p["port"] for p in a["ports"]}
    assert "ch 1" in ports and "ch 2" in ports
    vars_ = {s["variable"] for s in a["series"]}
    assert "piezo_kpa" in vars_ and "inclin_deg" in vars_
    html = popup_html(a)
    assert "Geotécnico" in html
    assert "PZ-01" in html
    assert "copiar p/ inspeção" in html
    assert "km 380" in a["inspect_line"]


def test_mixed_color_and_layers():
    devices = [
        {
            "deviceName": "EWS1 km 10",
            "latitude": -3.0,
            "longitude": -44.0,
            "lastUpload": "2026-11-16T12:00:00Z",
            "sensors": [
                {"sensorType": "Rainfall", "uom": "mm", "channelNumber": 1},
                {"sensorType": "Water Level", "uom": "m", "channelNumber": 2},
            ],
        },
        {
            "deviceName": "A044",
            "latitude": -3.1,
            "longitude": -44.1,
            "sensors": [{"sensorType": "Rainfall", "uom": "mm", "channelNumber": 1}],
        },
    ]
    cat = build_map_catalog(devices, now=datetime(2026, 11, 16, 13, tzinfo=timezone.utc))
    by_id = {a["station_id"]: a for a in cat["assets"]}
    # EWS1 matches EWS\\d+
    mixed = next(a for a in cat["assets"] if a["asset_class"] == "mixed")
    assert mixed["color"] == COLOR_MIXED
    assert mixed["layer"] == "mixed"
    rain = next(a for a in cat["assets"] if a["asset_class"] == "meteo")
    assert rain["color"] == COLOR_METEO
    assert cat["geojson"]["geotech"]["features"]
    assert cat["geojson"]["meteo"]["features"]
    # Mixed logger is on both toggles (chuva + geotécnico).
    mixed_ids_g = {f["properties"]["station_id"] for f in cat["geojson"]["geotech"]["features"]}
    mixed_ids_m = {f["properties"]["station_id"] for f in cat["geojson"]["meteo"]["features"]}
    assert mixed["station_id"] in mixed_ids_g and mixed["station_id"] in mixed_ids_m
    del by_id


def test_catalog_cli_fixture(tmp_path):
    out = tmp_path / "orion_devices.json"
    import argparse

    rc = cmd_catalog(
        argparse.Namespace(
            fixture="scripts/orion_ews/fixtures/user_devices_sample.json",
            out=str(out),
            sensors_out=None,
            serial="",
            from_secret=False,
        )
    )
    assert rc == 0
    import json

    data = json.loads(out.read_text())
    classes = {s["station_id"]: s["asset_class"] for s in data["summary"]}
    assert classes["EWS6536"] == "mixed"
    assert classes["EWS8801"] == "geotech"
    map_path = tmp_path / "orion_devices_map_assets.json"
    assert map_path.exists()
    cat = json.loads(map_path.read_text())
    assert cat["counts"]["geotech"] >= 1
    assert cat["siting"]["pattern"] == "corridor"


def test_siting_corridor_vs_yard():
    corridor = [
        {"deviceName": "EWS1 Abrigo km 149", "latitude": -3.54, "longitude": -44.89},
        {"deviceName": "EWS2 Abrigo km 629", "latitude": -5.0, "longitude": -47.4},
        {"deviceName": "EWS3 Abrigo km 807", "latitude": -5.7, "longitude": -49.3},
    ]
    r = siting_report(corridor)
    assert r["pattern"] == "corridor"
    assert r["km_min"] == 149 and r["km_max"] == 807
    assert r["n_in_efc_bbox"] == 3

    yard = [
        {"deviceName": "PZ pátio", "latitude": -5.37, "longitude": -49.12},
        {"deviceName": "INC pátio", "latitude": -5.372, "longitude": -49.121},
    ]
    y = siting_report(yard)
    assert y["pattern"] == "cluster"
