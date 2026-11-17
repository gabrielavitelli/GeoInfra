import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.http import QueryDict
from django.test import SimpleTestCase, TestCase

import numpy as np
import pandas as pd

from monitor_web.services.home_accumulated_alerts import (
    parse_accumulated_period,
    period_label_pt,
    period_range_pt,
    period_stats_from_df,
)
from monitor_web.services.home_indicators_html import indicators_panel_html
from monitor_web.services.home_impact_enrich import enrich_home_impact
from lib_efc import _station_csv_usable, load_bndmet_estacoes, next_pipeline_run_iso, next_pipeline_run_local_str
from monitor_web.services.boletins import (
    _comparison_table_rows,
    _day_series_for_chart,
    _default_occurrence_date,
    _occurrence_days_from_daily,
    build_boletim_dia_distribuicao_payload,
)
from gcp.bq_raijin import (
    RAIJIN_BOLETIM_NOMINAL_MM,
    build_boletim_comparison_summary,
    build_boletim_ev_comparison_df,
    raijin_mm_display_value,
    station_vs_alert_outcome,
)
from monitor_web.services.bq_cache import boletim_cache_key
from monitor_web.services.bq_status import bq_warning_message
from monitor_web.services.previsao import (
    _METRIC_HELP_PT,
    _build_forecast_fig,
    _build_heatmap_fig,
    _forecast_envelope_by_day,
    _forecast_series_for_model,
    _heatmap_km_step,
    _heatmap_today_iso,
    _horizon_caption,
    _obs_label,
    _previsao_verify_precompute_hint,
    _previsao_window_iso,
    _verification_km_step,
    _verification_payload_from_bq,
    _verification_payload_from_cache_table,
    build_previsao_heatmap_payload,
    parse_horizon_days,
    parse_horizon_days_with_max,
    parse_heatmap_horizon_window,
    parse_horizon_window,
)
from gcp.bq_reader_extended import (
    VERIFICATION_EVENT_THRESHOLD_MM,
    _boletim_hourly_distribution_usable,
    _boletim_merge_day_from_var_rows,
    _boletim_merge_day_from_wide_row,
    _merge_daily_mm_sql,
    _verification_metrics_from_long_df,
    _verification_periods_from_long_df,
    merge_daily_obs_series_for_km_bq,
    compute_pair_skill_metrics,
    verification_calendar_date_range,
    verification_event_threshold_mm,
)


class SuscetibilidadeTests(SimpleTestCase):
    def test_portal_alerts_source_defaults_merge(self):
        from django.test import RequestFactory

        from monitor_web.services.home_fragments import alerts_source_for_request

        rf = RequestFactory()
        self.assertEqual(alerts_source_for_request(rf.get("/portal/")), "merge")
        self.assertEqual(alerts_source_for_request(rf.get("/fragments/home/alerts/")), "raijin")

    def test_limiar_kind_distinguishes_raijin_tp_fp(self):
        from lib_efc import is_raijin_limiar_kind, limiar_kind_from_label

        self.assertEqual(limiar_kind_from_label("Raijin TP"), "raijin_tp")
        self.assertEqual(limiar_kind_from_label("Raijin FP"), "raijin_fp")
        self.assertEqual(limiar_kind_from_label("Raijin"), "raijin")
        self.assertTrue(is_raijin_limiar_kind("raijin_tp"))
        self.assertTrue(is_raijin_limiar_kind("raijin_fp"))
        self.assertFalse(is_raijin_limiar_kind("24h"))

    def test_raijin_alert_markers_prefer_tp_and_style(self):
        from monitor_web.services.map_alert_markers import alert_markers_from_df, radius_for_alert_mm
        from monitor_web.services.suscetibilidade import ALERT_KIND_COLORS, build_alert_cards_html

        df = pd.DataFrame(
            [
                {"Quilometragem (km)": 10, "Valor (mm)": 50, "Limiar": "Raijin FP"},
                {"Quilometragem (km)": 20, "Valor (mm)": 50, "Limiar": "Raijin TP"},
                {"Quilometragem (km)": 30, "Valor (mm)": 50, "Limiar": "Raijin"},
                {"Quilometragem (km)": 40, "Valor (mm)": 50, "Limiar": "Raijin FP"},
                {"Quilometragem (km)": 50, "Valor (mm)": 50, "Limiar": "Raijin FP"},
            ]
        )
        mk_all = alert_markers_from_df(df, raijin_only=True, tp_only=False)
        by_km = {km: kind for km, _mm, kind in mk_all}
        self.assertEqual(by_km[20], "raijin_tp")
        self.assertEqual(by_km[10], "raijin_fp")
        mk_tp = alert_markers_from_df(df, raijin_only=True, tp_only=True)
        self.assertEqual([k for k, _, kind in mk_tp], [20])
        self.assertGreater(radius_for_alert_mm(50, "raijin_tp"), radius_for_alert_mm(50, "raijin_fp"))
        self.assertGreaterEqual(radius_for_alert_mm(50, "raijin_tp"), 14)
        self.assertEqual(ALERT_KIND_COLORS["raijin_tp"], "#22d3ee")
        html = build_alert_cards_html(df, lang_pt=True, lookup=[])
        self.assertIn("Raijin TP", html)
        self.assertNotIn("Raijin FP", html)
        self.assertNotIn("solo ", html)

    def test_soil_chip_only_on_rain_alert_pilot_km(self):
        from monitor_web.services.solo_umidade import soil_micro_hint
        from monitor_web.services.suscetibilidade import build_alert_cards_html

        self.assertEqual(soil_micro_hint(352, lang_pt=True, kind="24h"), "solo normal")
        self.assertIsNone(soil_micro_hint(352, lang_pt=True, kind="raijin_tp"))
        self.assertIsNone(soil_micro_hint(739, lang_pt=True, kind="24h"))
        df = pd.DataFrame(
            [
                {"Quilometragem (km)": 352, "Valor (mm)": 63, "Limiar": "24h"},
                {"Quilometragem (km)": 10, "Valor (mm)": 55, "Limiar": "24h"},
            ]
        )
        html = build_alert_cards_html(df, lang_pt=True, lookup=[])
        self.assertIn("solo normal", html)
        self.assertIn("km 352", html)
        # km 10 não tem piloto SoilGrids
        self.assertEqual(html.count("solo "), 1)

    def test_alerts_panel_heading_names_source(self):
        from monitor_web.services.home_fragments import alerts_panel_heading

        self.assertIn("Raijin", alerts_panel_heading("raijin", lang_pt=True, n_kms=12))
        self.assertIn("12 km", alerts_panel_heading("raijin", lang_pt=True, n_kms=12))
        self.assertIn("Trechos", alerts_panel_heading("merge", lang_pt=True, n_kms=3))

    def test_exceed_table_heading_uses_selected_date_not_hoje(self):
        from monitor_web.services.home_fragments import exceed_table_heading

        t = exceed_table_heading("raijin", lang_pt=True, selected_display="13/03/2026")
        self.assertIn("Raijin", t)
        self.assertIn("13/03/2026", t)
        self.assertNotIn("hoje", t.lower())

    def test_band_from_criticality(self):
        from monitor_web.services.suscetibilidade import susc_band_from_criticality

        self.assertEqual(susc_band_from_criticality("Alta"), "alta")
        self.assertEqual(susc_band_from_criticality("Média"), "media")
        self.assertIsNone(susc_band_from_criticality("Baixa"))

    def test_lookup_and_micro_hint(self):
        from monitor_web.services.suscetibilidade import (
            build_rail_segment_lookup,
            lookup_segment_at_km,
            susc_micro_hint,
        )

        df = pd.DataFrame(
            [
                {
                    "segment_id": "A1",
                    "segment_name": "Trecho A",
                    "km_start": 100.0,
                    "km_end": 110.0,
                    "risk_class": "R3",
                    "criticality_class": "Alta",
                    "side": "Direito",
                    "attrs_json": '{"Tipo": "Aterro", "Suscetibilidade_ITV": "ITV-2"}',
                }
            ]
        )
        lookup = build_rail_segment_lookup(df)
        seg = lookup_segment_at_km(105, lookup)
        self.assertEqual(seg["risk_class"], "R3")
        self.assertEqual(susc_micro_hint(seg, lang_pt=True), "susc. alta")

    def test_align_indicators_portal_fast_loads_station_panel_sync(self):
        """portal_fast não pode só peek+thread: miss de cache deixava Estação 0 mm em prod."""
        from django.test import RequestFactory

        from monitor_web.services.home_fragments import _align_indicators_impact

        rf = RequestFactory()
        req = rf.get("/portal/?accum=720")
        panel = {
            "station_panel_peaks": {
                "24h": {"mm": 12.0, "station_id": "A044", "source": "bndmet", "n_above": 0, "n_total": 10},
                "5d": {"mm": 20.0, "station_id": "A044", "source": "bndmet", "n_above": 0, "n_total": 10},
                "7d": {"mm": 25.0, "station_id": "A044", "source": "bndmet", "n_above": 0, "n_total": 10},
                "30d": {"mm": 36.0, "station_id": "A044", "source": "bndmet", "n_above": 0, "n_total": 10},
            },
            "station_panel_entorno_peaks": {
                "30d": {"mm": 32.0, "station_id": "EWS6737", "source": "ews", "station_name": "Abrigo"},
            },
        }
        with patch(
            "monitor_web.services.home_fragments._station_panel_windows",
            return_value=panel,
        ) as mock_panel:
            out, _n = _align_indicators_impact(
                {"available": True, "lite": True},
                request=req,
                dataset="efc_clima",
                sel="2026-11-06",
                lang_pt=True,
                km_max=877,
                accum_hours=720,
                portal_fast=True,
            )
        mock_panel.assert_called_once()
        self.assertEqual(out["station_panel_peaks"]["30d"]["mm"], 36.0)
        self.assertEqual(out["station_panel_peaks"]["30d"]["station_id"], "A044")
        self.assertEqual(out["station_panel_entorno_peaks"]["30d"]["mm"], 32.0)

    def test_indicators_severity_uses_stations_not_merge_km(self):
        """Painel (exceto Raijin) conta estações; susc. de trecho MERGE não aparece."""
        impact = {
            "available": True,
            "total_km": 877,
            "n_km_24_envelope": 0,
            "n_km_120_envelope": 0,
            "n_km_7d_envelope": 76,
            "n_km_30d_envelope": 206,
            "pct_km_24_envelope": 0.0,
            "pct_km_120_envelope": 0.0,
            "pct_km_7d_envelope": 9.0,
            "pct_km_30d_envelope": 23.0,
            "max24_ensemble_mm": 80.0,
            "max24_ensemble_km": 400,
            "pct_km_agree_2plus": 0.0,
            "pct_km_agree_3plus": 0.0,
            "pct_km_24_two_models": 0.0,
            "pct_km_24_three_models": 0.0,
            "station_panel_peaks": {
                "24h": {"mm": 10.0, "station_id": "X1", "source": "bndmet", "n_above": 0, "n_total": 20},
                "5d": {"mm": 40.0, "station_id": "X2", "source": "bndmet", "n_above": 0, "n_total": 20},
                "7d": {"mm": 90.0, "station_id": "X3", "source": "ana", "n_above": 4, "n_total": 20},
                "30d": {"mm": 200.0, "station_id": "X4", "source": "bndmet", "n_above": 5, "n_total": 20},
            },
        }
        html = indicators_panel_html(
            lang_pt=True,
            impact=impact,
            th24=49.0,
            th120=250.0,
            exceed_susc_stats={
                "24h": {"n": 0, "n_susc": 0},
                "5d": {"n": 0, "n_susc": 0},
                "7d": {"n": 76, "n_susc": 19},
                "30d": {"n": 200, "n_susc": 50},
            },
        )
        self.assertIn("Estações acima do limiar", html)
        self.assertIn("20% (4 est.)", html)
        self.assertIn("25% (5 est.)", html)
        self.assertNotIn("Trechos em Alerta", html)
        self.assertNotIn("susc. dos em alerta", html)
        self.assertNotIn("incl. susc.", html)

    def test_indicators_fallback_to_railway_when_stations_calm(self):
        """Estações em 0% não escondem contagens de TPs; cards mm continuam com estação."""
        impact = {
            "available": True,
            "total_km": 877,
            "n_km_24_envelope": 2,
            "n_km_120_envelope": 4,
            "n_km_7d_envelope": 5,
            "n_km_30d_envelope": 12,
            "pct_km_24_envelope": 0.2,
            "max24_ensemble_mm": 51.4,
            "max24_ensemble_km": 371,
            "pct_km_agree_2plus": 0.0,
            "pct_km_agree_3plus": 0.0,
            "pct_km_24_two_models": 0.0,
            "pct_km_24_three_models": 0.0,
            "station_panel_peaks": {
                "24h": {"mm": 10.0, "station_id": "X1", "source": "bndmet", "n_above": 0, "n_total": 20},
                "5d": {"mm": 20.0, "station_id": "X2", "source": "bndmet", "n_above": 0, "n_total": 20},
                "7d": {"mm": 30.0, "station_id": "X3", "source": "ana", "n_above": 0, "n_total": 20},
                "30d": {"mm": 40.0, "station_id": "X4", "source": "bndmet", "n_above": 0, "n_total": 20},
            },
            "period_label_pt": "últimos 30 dias · 12/02/2026 – 13/03/2026",
        }
        html = indicators_panel_html(lang_pt=True, impact=impact, th24=49.0, th120=250.0)
        self.assertIn("Trechos acima do limiar", html)
        self.assertIn("2 km", html)
        self.assertIn("12 km", html)
        # Pico mm continua da estação (não troca por ensemble MERGE/modelo).
        self.assertIn("10", html)
        self.assertIn("BNDMET X1", html)
        self.assertNotIn("km 371", html)
        self.assertNotIn("0% (0 est.)", html)
        self.assertNotIn("Estações acima do limiar", html)

    def test_obs_card_follows_toolbar_accum_even_when_counts_use_km(self):
        """Com estações calmas, contagens vão para km; o card Observado segue a janela do menu."""
        from monitor_web.services.home_indicators_context import build_indicators_context

        impact = {
            "available": True,
            "total_km": 877,
            "n_km_24_envelope": 2,
            "n_km_120_envelope": 4,
            "n_km_7d_envelope": 5,
            "n_km_30d_envelope": 12,
            "bndmet_max_24_mm": 1.0,
            "ana_max_24_mm": 0.5,
            "station_panel_peaks": {
                "24h": {"mm": 1.0, "station_id": "32540000", "source": "ana", "n_above": 0, "n_total": 20},
                "5d": {"mm": 6.0, "station_id": "A255", "source": "bndmet", "n_above": 0, "n_total": 20},
                "7d": {"mm": 6.0, "station_id": "A255", "source": "bndmet", "n_above": 0, "n_total": 20},
                "30d": {"mm": 25.0, "station_id": "D6182", "source": "bndmet", "n_above": 0, "n_total": 20},
            },
        }
        ctx30 = build_indicators_context(
            lang_pt=True, impact=impact, th24=49.0, th120=250.0, accum_hours=720
        )
        self.assertEqual(ctx30["accum_kind"], "30d")
        self.assertEqual(ctx30["obs"]["mm"], 25.0)
        self.assertIn("D6182", ctx30["obs"]["name"])
        self.assertEqual(ctx30["peaks"][0]["accum_key"], "30d")
        self.assertTrue(ctx30["peaks"][0]["active"])

        ctx24 = build_indicators_context(
            lang_pt=True, impact=impact, th24=49.0, th120=250.0, accum_hours=24
        )
        self.assertEqual(ctx24["accum_kind"], "24h")
        self.assertEqual(ctx24["obs"]["mm"], 1.0)
        self.assertIn("32540000", ctx24["obs"]["name"])
        self.assertEqual(ctx24["peaks"][0]["accum_key"], "24h")


class IndicatorsPanelHtmlTests(SimpleTestCase):
    def test_peaks_show_station_not_merge_km(self):
        impact = {
            "available": True,
            "total_km": 877,
            "n_km_24_envelope": 50,
            "pct_km_24_envelope": 5.0,
            "pct_km_agree_2plus": 0.0,
            "pct_km_agree_3plus": 0.0,
            "pct_km_24_two_models": 0.0,
            "pct_km_24_three_models": 0.0,
            "max24_ensemble_mm": 999.0,
            "max24_ensemble_km": 333,
            "station_panel_peaks": {
                "24h": {
                    "mm": 61.0,
                    "station_id": "A001",
                    "source": "bndmet",
                    "n_above": 2,
                    "n_total": 10,
                },
                "5d": {"mm": 0.0, "station_id": None, "source": None, "n_above": 0, "n_total": 0},
                "7d": {"mm": 0.0, "station_id": None, "source": None, "n_above": 0, "n_total": 0},
                "30d": {"mm": 0.0, "station_id": None, "source": None, "n_above": 0, "n_total": 0},
            },
        }
        html = indicators_panel_html(lang_pt=True, impact=impact, th24=49.0, th120=250.0)
        self.assertIn("61", html)
        self.assertIn("BNDMET A001", html)
        self.assertNotIn("km 333", html)
        self.assertNotIn("999", html)
        self.assertIn("Raijin", html)

    def test_section_order_and_faixa_contains_limiar(self):
        impact = {
            "available": True,
            "total_km": 100,
            "n_km_24_envelope": 5,
            "n_km_120_envelope": 2,
            "n_km_7d_envelope": 1,
            "n_km_30d_envelope": 0,
            "pct_km_24_envelope": 5.0,
            "pct_km_120_envelope": 2.0,
            "pct_km_7d_envelope": 1.0,
            "pct_km_30d_envelope": 0.0,
            "pct_km_agree_2plus": 10.0,
            "pct_km_agree_3plus": 4.0,
            "pct_km_24_two_models": 7.0,
            "pct_km_24_three_models": 3.0,
            "bndmet_max_24_mm": 12.0,
            "ana_max_24_mm": 8.0,
            "station_panel_peaks": {
                "24h": {
                    "mm": 60.0,
                    "station_id": "A001",
                    "source": "bndmet",
                    "n_above": 2,
                    "n_total": 10,
                },
                "5d": {
                    "mm": 200.0,
                    "station_id": "A002",
                    "source": "bndmet",
                    "n_above": 1,
                    "n_total": 10,
                },
                "7d": {
                    "mm": 180.0,
                    "station_id": "A003",
                    "source": "ana",
                    "n_above": 1,
                    "n_total": 10,
                },
                "30d": {
                    "mm": 400.0,
                    "station_id": "A004",
                    "source": "bndmet",
                    "n_above": 3,
                    "n_total": 10,
                },
            },
            "period_label_pt": "últimos 90 dias · 01/01/2026 – 19/05/2026",
            "period_mode": True,
        }
        html = indicators_panel_html(lang_pt=True, impact=impact, th24=49.0, th120=250.0)
        self.assertIn("dj-module--metrics", html)
        self.assertIn("dj-compact-peaks", html)
        self.assertNotIn("Detalhes técnicos", html)
        self.assertNotIn("≥2 modelos acima de 49 mm", html)
        self.assertIn("efc-stitch-kpi-card__num", html)
        self.assertIn("60", html)
        self.assertIn("Acima do limiar", html)
        self.assertIn("24 h", html)
        self.assertIn("5 d", html)
        self.assertIn("7 d", html)
        self.assertIn("30 d", html)
        self.assertIn("est.", html)

    def test_observed_24h_shows_station_name_and_km(self):
        impact = {
            "available": True,
            "total_km": 10,
            "n_km_24_envelope": 0,
            "n_km_120_envelope": 0,
            "pct_km_24_envelope": 0.0,
            "pct_km_120_envelope": 0.0,
            "pct_km_agree_2plus": 0.0,
            "pct_km_agree_3plus": 0.0,
            "pct_km_24_two_models": 0.0,
            "pct_km_24_three_models": 0.0,
            "bndmet_max_24_mm": 70.0,
            "ana_max_24_mm": 1.0,
            "max24_ensemble_mm": None,
            "max24_ensemble_km": None,
            "max120_ensemble_mm": None,
            "max120_ensemble_km": None,
            "bndmet_station": {
                "title": "Estação BNDMET",
                "primary": "Estação Teste BND",
                "secondary": "cód. 12345 · km EFC 412",
            },
            "ana_station": {
                "title": "Estação ANA",
                "primary": "Estação Teste ANA",
                "secondary": "cód. 67890 · km EFC 55",
            },
        }
        html = indicators_panel_html(lang_pt=True, impact=impact, th24=49.0, th120=250.0)
        self.assertIn("Estação Teste BND", html)
        self.assertIn("70", html)
        self.assertIn("efc-stitch-kpi-card--obs", html)

    def test_entorno_card_shows_max_near_focus_km(self):
        from monitor_web.services.home_indicators_context import build_indicators_context

        impact = {
            "available": True,
            "total_km": 877,
            "n_km_24_envelope": 0,
            "n_km_120_envelope": 0,
            "n_km_7d_envelope": 0,
            "n_km_30d_envelope": 0,
            "station_panel_peaks": {
                "24h": {"mm": 90.0, "station_id": "FAR", "source": "bndmet", "n_above": 1, "n_total": 10},
                "5d": {"mm": 0.0, "station_id": None, "source": None, "n_above": 0, "n_total": 0},
                "7d": {"mm": 0.0, "station_id": None, "source": None, "n_above": 0, "n_total": 0},
                "30d": {"mm": 0.0, "station_id": None, "source": None, "n_above": 0, "n_total": 0},
            },
            "station_panel_entorno_peaks": {
                "24h": {
                    "mm": 42.0,
                    "station_id": "D6182",
                    "source": "bndmet",
                    "dist_km": 8.0,
                    "municipio": "PARAUAPEBAS-PA",
                    "station_name": "PARAUAPEBAS",
                    "km_efc": 856,
                    "n_in_radius": 3,
                },
                "5d": {"mm": 0.0, "station_id": None, "source": None, "n_in_radius": 0},
                "7d": {"mm": 0.0, "station_id": None, "source": None, "n_in_radius": 0},
                "30d": {"mm": 0.0, "station_id": None, "source": None, "n_in_radius": 0},
            },
            "entorno_mode": "km",
            "entorno_radius_km": 45.0,
            "entorno_focus_km": 856,
        }
        ctx = build_indicators_context(
            lang_pt=True, impact=impact, th24=49.0, th120=250.0, accum_hours=24
        )
        self.assertEqual(ctx["entorno"]["mm"], 42.0)
        self.assertIn("PARAUAPEBAS", ctx["entorno"]["name"])
        self.assertIn("8 km", ctx["entorno"]["name"])
        self.assertIn("km 856", ctx["entorno"]["focus_label"])
        html = indicators_panel_html(lang_pt=True, impact=impact, th24=49.0, th120=250.0)
        self.assertIn("Máximo no entorno", html)
        self.assertIn("PARAUAPEBAS", html)
        self.assertIn("efc-stitch-kpi-card--entorno", html)
        self.assertIn("42", html)


class IaInsightTests(SimpleTestCase):
    def test_build_ia_insight_critical_when_wn2_above_threshold(self):
        from monitor_web.services.efc_ia_insight import build_ia_insight_context

        ctx = build_ia_insight_context(
            lang_pt=True,
            impact={
                "available": True,
                "max24_wn2_mm": 62.0,
                "max24_wn2_km": 371,
                "n_km_wn2_ia_agree": 2,
            },
            goes={"available": True, "last_image_utc": "25-05-2026 12:00 UTC", "stale": False},
        )
        self.assertTrue(ctx["available"])
        self.assertEqual(ctx["tone"], "crit")
        self.assertTrue(ctx["agree_show"])
        self.assertIn("GOES-19", ctx["goes_line"])

    def test_indicators_panel_renders_ia_insight_block(self):
        impact = {
            "available": True,
            "total_km": 877,
            "n_km_24_envelope": 0,
            "n_km_120_envelope": 0,
            "n_km_7d_envelope": 0,
            "n_km_30d_envelope": 0,
            "pct_km_24_envelope": 0.0,
            "pct_km_120_envelope": 0.0,
            "pct_km_7d_envelope": 0.0,
            "pct_km_30d_envelope": 0.0,
            "max24_ensemble_mm": 55.0,
            "max24_ensemble_km": 400,
            "max120_ensemble_mm": None,
            "max120_ensemble_km": None,
            "max7d_ensemble_mm": None,
            "max7d_ensemble_km": None,
            "max30d_ensemble_mm": None,
            "max30d_ensemble_km": None,
            "pct_km_agree_2plus": 0.0,
            "pct_km_agree_3plus": 0.0,
            "pct_km_24_two_models": 0.0,
            "pct_km_24_three_models": 0.0,
            "max24_wn2_mm": 58.0,
            "max24_wn2_km": 379,
            "n_km_wn2_ia_agree": 1,
        }
        html = indicators_panel_html(lang_pt=True, impact=impact, th24=49.0, th120=250.0)
        self.assertIn("dj-ia-insight", html)
        self.assertIn("Previsão IA (WN2)", html)
        self.assertIn("WN2+globais", html)


class HomeImpactWn2MetricsTests(SimpleTestCase):
    def test_home_impact_wn2_metrics_counts_ia_agreement(self):
        import pandas as pd

        from gcp.bq_reader_extended import _home_impact_wn2_metrics

        pivot24 = pd.DataFrame(
            {
                "ecmwf": [50.0, 10.0],
                "gfs": [52.0, 5.0],
                "icon": [48.0, 8.0],
                "weathernext2": [55.0, 20.0],
            },
            index=[100, 200],
        )
        out = _home_impact_wn2_metrics(pivot24, threshold_24=49.0, total_km=2)
        self.assertEqual(out["max24_wn2_mm"], 55.0)
        self.assertEqual(out["max24_wn2_km"], 100)
        self.assertEqual(out["n_km_wn2_ia_agree"], 1)


class HomeImpactEnrichTests(SimpleTestCase):
    def test_enrich_resolves_km_from_coordinates(self):
        main_line = [(0.0, 0.0), (0.0, 1.0)]
        cum_km = [0.0, 100.0]
        impact = {
            "available": True,
            "bndmet_max_24_id": "12345",
            "ana_max_24_id": "67890",
        }
        bnd_lookup = {
            "12345": {
                "code": "12345",
                "name": "Estação BND",
                "lat": 0.0,
                "lon": 0.5,
                "km_efc": None,
            }
        }
        ana_lookup = {
            "67890": {
                "code": "67890",
                "name": "Estação ANA",
                "lat": 0.0,
                "lon": 0.25,
                "km_efc": None,
            }
        }

        with patch(
            "monitor_web.services.home_impact_enrich._cached_lookups",
            return_value={"bndmet": bnd_lookup, "ana": ana_lookup},
        ):
            out = enrich_home_impact(
                impact,
                lang_pt=True,
                main_line=main_line,
                cum_km=cum_km,
            )

        self.assertIn("km EFC", out["bndmet_station"]["secondary"])
        self.assertIn("km EFC", out["ana_station"]["secondary"])


class MapObservationCaptionTests(SimpleTestCase):
    def test_summary_with_accumulated_alerts_pt(self):
        from monitor_web.services.html_ui import map_observation_summary_label

        label = map_observation_summary_label(
            lang_pt=True,
            selected_display="19/05/2026",
            accum_hours=24,
            use_bq=True,
            acc_period="90",
            acc_alerts_on=True,
        )
        self.assertIn("19/05/2026", label)
        self.assertIn("janela 24 h", label)
        self.assertIn("BNDMET/ANA", label)
        self.assertIn("alertas acumulados", label)
        self.assertIn("últimos 90 dias", label)
        self.assertNotIn("acumulado nas cores dos pontos", label)

    def test_summary_without_alerts_en(self):
        from monitor_web.services.html_ui import map_observation_summary_label

        label = map_observation_summary_label(
            lang_pt=False,
            selected_display="05/19/2026",
            accum_hours=120,
            use_bq=False,
            acc_alerts_on=False,
        )
        self.assertIn("5 D window", label)
        self.assertNotIn("accumulated alerts", label)


class StationCatalogTests(SimpleTestCase):
    def test_empty_csv_not_usable(self):
        df = pd.DataFrame(columns=["codEstacao", "latitude", "longitude"])
        self.assertFalse(_station_csv_usable(df))

    def test_bq_fallback_when_csv_header_only(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            csv = Path(td) / "bndmet_estacoes_efc.csv"
            csv.write_text("codEstacao,latitude,longitude\n", encoding="utf-8")
            with patch(
                "lib_efc._load_station_catalog_from_bq",
                return_value=pd.DataFrame(
                    {
                        "codEstacao": ["1"],
                        "latitude": [-23.0],
                        "longitude": [-46.0],
                        "nome": ["A"],
                    }
                ),
            ):
                df = load_bndmet_estacoes(csv, bq_dataset="efc_clima")
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 1)


class MergeBubbleBandTests(SimpleTestCase):
    def test_band_merge_24h_days(self):
        from monitor_web.services.home_accumulated_alerts import band_for_merge_24h_days

        self.assertEqual(band_for_merge_24h_days(0), "none")
        self.assertEqual(band_for_merge_24h_days(1), "low")
        self.assertEqual(band_for_merge_24h_days(3), "medium")
        self.assertEqual(band_for_merge_24h_days(5), "high")
        self.assertEqual(band_for_merge_24h_days(6), "critical")

    def test_yellow_bubbles_grow_with_days(self):
        from monitor_web.services.home_accumulated_alerts import (
            _BUBBLE_YELLOW,
            _BAND_COLORS,
            radius_for_n_alertas,
        )

        self.assertTrue(all(c == _BUBBLE_YELLOW for c in _BAND_COLORS.values()))
        self.assertLess(radius_for_n_alertas(1), radius_for_n_alertas(3))
        self.assertLess(radius_for_n_alertas(3), radius_for_n_alertas(5))
        self.assertLess(radius_for_n_alertas(5), radius_for_n_alertas(8))


class GroupExceedTableRowsTests(SimpleTestCase):
    def test_groups_consecutive_km_same_limiar_and_mm(self):
        from lib_efc import group_exceed_table_rows

        rows = [
            {"Limiar": "24h > 49 mm", "Km": 13.0, "Valor": 74.0},
            {"Limiar": "24h > 49 mm", "Km": 14.0, "Valor": 74.0},
            {"Limiar": "24h > 49 mm", "Km": 16.0, "Valor": 74.0},
            {"Limiar": "24h > 49 mm", "Km": 41.0, "Valor": 55.7},
        ]
        out = group_exceed_table_rows(rows)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["Km"], "13–14")
        self.assertEqual(out[1]["Km"], "16")
        self.assertEqual(out[2]["Valor"], 55.7)

    def test_splits_on_mm_change(self):
        from lib_efc import group_exceed_table_rows

        rows = [
            {"Limiar": "24h > 49 mm", "Quilometragem (km)": 23.0, "Valor (mm)": 84.8},
            {"Limiar": "24h > 49 mm", "Quilometragem (km)": 24.0, "Valor (mm)": 84.8},
            {"Limiar": "24h > 49 mm", "Quilometragem (km)": 33.0, "Valor (mm)": 101.3},
        ]
        out = group_exceed_table_rows(rows)
        self.assertEqual(out[0]["Km"], "23–24")
        self.assertEqual(out[1]["Km"], "33")


class ParseAccumulatedPeriodTests(SimpleTestCase):
    def _req(self, query: str):
        class R:
            GET = QueryDict(query)

        return R()

    def test_days_default_30_when_unspecified(self):
        today = date.today()
        d0, d1, key = parse_accumulated_period(self._req(""))
        self.assertEqual(key, "days_30")
        self.assertEqual(d1, today.isoformat())
        self.assertEqual(d0, (today - __import__("datetime").timedelta(days=29)).isoformat())

    def test_accum_menu_maps_to_bubble_days(self):
        cases = (("24", "days_1"), ("120", "days_5"), ("168", "days_7"), ("720", "days_30"))
        for accum, want in cases:
            _, _, key = parse_accumulated_period(self._req(f"accum={accum}"))
            self.assertEqual(key, want, msg=f"accum={accum}")

    def test_toolbar_date_is_period_end(self):
        d0, d1, key = parse_accumulated_period(self._req("date=2026-06-15&days=5"))
        self.assertEqual(key, "days_5")
        self.assertEqual(d1, "2026-06-15")
        self.assertEqual(d0, "2026-06-11")

    def test_days_90_rolling(self):
        today = date.today()
        d0, d1, key = parse_accumulated_period(self._req("days=90"))
        self.assertEqual(key, "days_90")
        self.assertEqual(d1, today.isoformat())
        self.assertEqual(d0, (today - __import__("datetime").timedelta(days=89)).isoformat())

    def test_year_2026_ytd(self):
        today = date.today()
        d0, d1, key = parse_accumulated_period(self._req("year=2026"))
        self.assertEqual(key, "year_2026_ytd")
        self.assertEqual(d0, "2026-01-01")
        self.assertEqual(d1, min(today, date(2026, 12, 31)).isoformat())

    def test_period_year_2026_alias(self):
        d0, d1, key = parse_accumulated_period(self._req("period=year_2026"))
        self.assertEqual(key, "year_2026_ytd")
        self.assertEqual(d0, "2026-01-01")

    def test_days_year_2026_not_fallback_to_90(self):
        d0, d1, key = parse_accumulated_period(self._req("days=year_2026"))
        self.assertEqual(key, "year_2026_ytd")
        self.assertEqual(d0, "2026-01-01")

    def test_year_wins_over_days(self):
        d0, _, key = parse_accumulated_period(self._req("year=2026&days=90"))
        self.assertEqual(key, "year_2026_ytd")
        self.assertEqual(d0, "2026-01-01")

    def test_period_label_includes_range(self):
        label = period_label_pt("days_90", "2026-02-19", "2026-05-19")
        self.assertIn("90", label)
        self.assertIn("19/02/2026", label)
        self.assertIn("19/05/2026", label)

    def test_period_label_year_ytd(self):
        label = period_label_pt("year_2026_ytd", "2026-01-01", "2026-05-19")
        self.assertIn("2026", label)
        self.assertIn("01/01/2026", label)

    def test_period_range_pt(self):
        self.assertEqual(period_range_pt("2026-02-19", "2026-05-19"), "19/02/2026 – 19/05/2026")

    def test_period_stats_from_df(self):
        df = pd.DataFrame(
            [
                {"km": 1, "n_alertas": 3, "civil_days_with_alert": 42, "km_day_occurrences": 1711},
                {"km": 2, "n_alertas": 5, "civil_days_with_alert": 42, "km_day_occurrences": 1711},
            ]
        )
        civil, km_day = period_stats_from_df(df)
        self.assertEqual(civil, 42)
        self.assertEqual(km_day, 1711)

    def test_period_stats_empty_df(self):
        self.assertEqual(period_stats_from_df(None), (0, 0))
        self.assertEqual(period_stats_from_df(pd.DataFrame()), (0, 0))

    def test_custom_start_end_range(self):
        d0, d1, key = parse_accumulated_period(
            self._req("period_mode=custom&start=2026-01-10&end=2026-03-15")
        )
        self.assertEqual(key, "range_2026-01-10_2026-03-15")
        self.assertEqual(d0, "2026-01-10")
        self.assertEqual(d1, "2026-03-15")

    def test_custom_swaps_inverted_dates(self):
        d0, d1, key = parse_accumulated_period(
            self._req("start=2026-05-01&end=2026-02-01")
        )
        self.assertEqual(d0, "2026-02-01")
        self.assertEqual(d1, "2026-05-01")
        self.assertTrue(key.startswith("range_"))

    def test_period_alias_date_start_end(self):
        d0, d1, key = parse_accumulated_period(
            self._req("date_start=2026-04-01&date_end=2026-04-30")
        )
        self.assertEqual(d0, "2026-04-01")
        self.assertEqual(d1, "2026-04-30")
        self.assertEqual(key, "range_2026-04-01_2026-04-30")

    def test_period_label_custom_range(self):
        label = period_label_pt("range_2026-01-01_2026-05-19", "2026-01-01", "2026-05-19")
        self.assertIn("01/01/2026", label)
        self.assertIn("19/05/2026", label)

    def test_indicators_period_query_string(self):
        from monitor_web.services.home_accumulated_alerts import indicators_period_query_string

        qs = indicators_period_query_string(
            self._req("period_mode=custom&start=2026-02-01&end=2026-02-28")
        )
        self.assertIn("period_mode=custom", qs)
        self.assertIn("start=2026-02-01", qs)
        self.assertIn("end=2026-02-28", qs)

    def test_invalid_days_defaults_30_not_90(self):
        today = date.today()
        d0, d1, key = parse_accumulated_period(self._req("days=not-a-number"))
        self.assertEqual(key, "days_30")
        self.assertEqual(d0, (today - __import__("datetime").timedelta(days=29)).isoformat())

    def test_period_mode_30_ignores_start_end_fields(self):
        today = date.today()
        d0, d1, key = parse_accumulated_period(
            self._req("period_mode=30&start=2020-01-01&end=2020-12-31&days=30")
        )
        self.assertEqual(key, "days_30")
        self.assertEqual(d1, today.isoformat())
        self.assertEqual(d0, (today - __import__("datetime").timedelta(days=29)).isoformat())

    def test_date_end_cap_clamps_period_to_bq(self):
        d0, d1, key = parse_accumulated_period(
            self._req("days=30"), date_end_cap="2026-03-13"
        )
        self.assertEqual(key, "days_30")
        self.assertEqual(d1, "2026-03-13")
        self.assertEqual(d0, "2026-02-12")

    def test_date_end_cap_clamps_toolbar_date_after_bq(self):
        _, d1, key = parse_accumulated_period(
            self._req("date=2026-08-18&days=30"), date_end_cap="2026-03-13"
        )
        self.assertEqual(key, "days_30")
        self.assertEqual(d1, "2026-03-13")


class ImpactLiteRaijinTests(SimpleTestCase):
    def test_raijin_tp_counts_as_24h_skips_fp(self):
        from monitor_web.services.impact_lite import impact_lite_from_day_exceed

        df = pd.DataFrame(
            [
                {"Quilometragem (km)": 371, "Valor (mm)": 51.4, "Limiar": "Raijin TP"},
                {"Quilometragem (km)": 376, "Valor (mm)": 50.0, "Limiar": "Raijin TP"},
                {"Quilometragem (km)": 10, "Valor (mm)": 80.0, "Limiar": "Raijin FP"},
            ]
        )
        out = impact_lite_from_day_exceed(df, km_max=877, lang_pt=True)
        self.assertEqual(out["n_km_24_envelope"], 2)
        self.assertAlmostEqual(out["max24_ensemble_mm"], 51.4)
        self.assertEqual(out["max24_ensemble_km"], 371)

    @patch("monitor_web.services.home_accumulated_alerts.raijin_tp_km_count")
    def test_apply_raijin_period_envelopes_uses_map_window(self, mock_n):
        from monitor_web.services.home_accumulated_alerts import apply_raijin_period_envelopes

        def _n(_ds, start, end):
            if start == end == "2026-03-13":
                return 2
            if start == "2026-03-09":
                return 3
            if start == "2026-03-07":
                return 4
            if start == "2026-02-12":
                return 12
            return 0

        mock_n.side_effect = _n
        out = apply_raijin_period_envelopes(
            {"available": True},
            dataset="efc",
            date_start="2026-02-12",
            date_end="2026-03-13",
            period_key="days_30",
            km_max=877,
            n_km_24=2,
        )
        self.assertEqual(out["n_km_24_envelope"], 2)
        self.assertEqual(out["n_km_120_envelope"], 3)
        self.assertEqual(out["n_km_7d_envelope"], 4)
        self.assertEqual(out["n_km_30d_envelope"], 12)


class ParseHorizonDaysTests(SimpleTestCase):
    def test_clamps_high(self):
        self.assertEqual(parse_horizon_days("99"), 15)

    def test_clamps_low(self):
        self.assertEqual(parse_horizon_days("1"), 3)

    def test_default(self):
        self.assertEqual(parse_horizon_days(None), 10)


class PrevisaoWindowLabelTests(SimpleTestCase):
    def test_obs_labels(self):
        self.assertIn("MERGE", _obs_label("merge"))
        self.assertIn("BNDMET", _obs_label("bndmet"))

    def test_horizon_caption(self):
        self.assertIn("10", _horizon_caption(10, 10))

    def test_window_iso_symmetric(self):
        start, end = _previsao_window_iso(5, 7)
        self.assertRegex(start, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(end, r"^\d{4}-\d{2}-\d{2}$")
        self.assertLess(start, end)


class MergeHourlyDownloadPathTests(SimpleTestCase):
    def test_url_includes_day_folder(self):
        from datetime import datetime, timezone

        from downloads.download_merge_hourly_range import merge_hourly_grib_paths

        dt = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
        url, ftp, rel = merge_hourly_grib_paths(dt)
        self.assertIn("/2026/01/01/MERGE_CPTEC_2026010100.grib2", url)
        self.assertIn("/2026/01/01/", ftp)
        self.assertEqual(str(rel), "2026/01/01/MERGE_CPTEC_2026010100.grib2")


class ParseHorizonWindowTests(SimpleTestCase):
    def _req(self, query: str):
        class R:
            GET = QueryDict(query)

        return R()

    def test_legacy_horizon_sets_both(self):
        past, future = parse_horizon_window(self._req("horizon=12"))
        self.assertEqual(past, 12)
        self.assertEqual(future, 12)

    def test_explicit_past_future(self):
        past, future = parse_horizon_window(self._req("horizon_past=8&horizon_future=5"))
        self.assertEqual(past, 8)
        self.assertEqual(future, 5)

    def test_default_future_is_15_days(self):
        past, future = parse_horizon_window(self._req(""))
        self.assertEqual(past, 10)
        self.assertEqual(future, 15)


class ParseHeatmapHorizonTests(SimpleTestCase):
    def _req(self, query: str = ""):
        class R:
            GET = QueryDict(query)

        return R()

    def test_horizon_days_with_max_allows_above_15(self):
        self.assertEqual(parse_horizon_days_with_max("40", default=10, max_days=60), 40)

    @patch("gcp.bq_reader_extended.get_previsao_forecast_span_days_bq", return_value=(12, 28))
    def test_default_uses_bq_span_capped_at_15_future(self, mock_span):
        past, future = parse_heatmap_horizon_window(self._req(""), dataset="efc_clima")
        self.assertEqual((past, future), (12, 15))
        mock_span.assert_called_once_with(dataset="efc_clima")

    def test_explicit_query_clamped_to_heatmap_max(self):
        past_max, future_max = 45, 60
        with patch("monitor_web.services.previsao._heatmap_horizon_max", return_value=(past_max, future_max)):
            past, future = parse_heatmap_horizon_window(
                self._req("horizon_past=99&horizon_future=99"), dataset="efc_clima"
            )
        self.assertEqual(past, past_max)
        self.assertEqual(future, future_max)


class ComputePairSkillMetricsTests(SimpleTestCase):
    def test_perfect_detection(self):
        import numpy as np

        obs = np.array([50.0, 0.0, 60.0, 2.0])
        fc = np.array([55.0, 1.0, 70.0, 0.0])
        m = compute_pair_skill_metrics(obs, fc, threshold_mm=49.0)
        self.assertEqual(m["n_pairs"], 4)
        self.assertEqual(m["hits"], 2)
        self.assertEqual(m["misses"], 0)
        self.assertEqual(m["false_alarms"], 0)
        self.assertEqual(m["tn"], 2)
        self.assertAlmostEqual(m["pod"], 1.0)
        self.assertAlmostEqual(m["far"], 0.0)

    def test_event_at_5mm_default_threshold(self):
        import numpy as np

        obs = np.array([6.0, 4.0, 12.0, 0.0])
        fc = np.array([7.0, 8.0, 3.0, 0.0])
        m = compute_pair_skill_metrics(obs, fc, threshold_mm=5.0)
        self.assertEqual(m["n_pairs"], 4)
        self.assertEqual(m["hits"], 1)
        self.assertEqual(m["misses"], 1)
        self.assertEqual(m["false_alarms"], 1)
        self.assertEqual(m["tn"], 1)
        self.assertAlmostEqual(m["pod"], 0.5)
        self.assertAlmostEqual(m["far"], 0.5)

    def test_default_threshold_constant_is_5mm(self):
        import numpy as np

        self.assertEqual(VERIFICATION_EVENT_THRESHOLD_MM, 5.0)
        self.assertEqual(verification_event_threshold_mm(), 5.0)
        obs = np.array([5.0, 4.9])
        fc = np.array([5.0, 0.0])
        m = compute_pair_skill_metrics(obs, fc)
        self.assertEqual(m["hits"], 1)
        self.assertEqual(m["tn"], 1)


class BoletimDaySeriesTests(SimpleTestCase):
    def test_hourly_kept_as_24_steps(self):
        import pandas as pd

        hourly = pd.DataFrame(
            [{"label": f"{h:02d}h", "precip_step_mm": float(h)} for h in range(24)]
        )
        hourly["accum_mm"] = hourly["precip_step_mm"].cumsum()
        out, kind, bar = _day_series_for_chart(hourly, "1h")
        self.assertEqual(kind, "1h")
        self.assertEqual(bar, "Chuva 1 h")
        self.assertEqual(len(out), 24)
        self.assertAlmostEqual(float(out.iloc[3]["precip_step_mm"]), 3.0)

    def test_24h_flat_renders_as_four_equal_6h_bars(self):
        import pandas as pd

        flat = pd.DataFrame(
            [
                {"label": "00–06", "precip_step_mm": 12.0},
                {"label": "06–12", "precip_step_mm": 12.0},
                {"label": "12–18", "precip_step_mm": 12.0},
                {"label": "18–24", "precip_step_mm": 12.0},
            ]
        )
        flat["accum_mm"] = flat["precip_step_mm"].cumsum()
        out, kind, bar = _day_series_for_chart(flat, "24h_flat")
        self.assertEqual(kind, "6h")
        self.assertEqual(len(out), 4)
        self.assertEqual(bar, "Passo 6 h (estimado)")

    def test_6h_unchanged(self):
        import pandas as pd

        six = pd.DataFrame(
            [
                {"label": "00h", "precip_step_mm": 10.0},
                {"label": "06h", "precip_step_mm": 5.0},
                {"label": "12h", "precip_step_mm": 0.0},
                {"label": "18h", "precip_step_mm": 2.0},
            ]
        )
        six["accum_mm"] = six["precip_step_mm"].cumsum()
        out, kind, bar = _day_series_for_chart(six, "6h")
        self.assertEqual(kind, "6h")
        self.assertEqual(len(out), 4)
        self.assertEqual(bar, "Passo 6 h")


class PrevisaoHeatmapKmStepTests(SimpleTestCase):
    def test_defaults_differ(self):
        with patch.dict("os.environ", {}, clear=False):
            for key in ("PREVISAO_HEATMAP_KM_STEP", "PREVISAO_VERIFICATION_KM_STEP"):
                os.environ.pop(key, None)
            self.assertEqual(_heatmap_km_step(), 10)
            self.assertEqual(_verification_km_step(), 5)

    def test_env_overrides(self):
        with patch.dict(
            "os.environ",
            {"PREVISAO_HEATMAP_KM_STEP": "8", "PREVISAO_VERIFICATION_KM_STEP": "3"},
        ):
            self.assertEqual(_heatmap_km_step(), 8)
            self.assertEqual(_verification_km_step(), 3)


class PrevisaoHeatmapPayloadTests(SimpleTestCase):
    def _req(self, query: str = ""):
        class R:
            GET = QueryDict(query or "horizon_past=10&horizon_future=10")

        return R()

    @patch("monitor_web.services.previsao._parse_previsao_heatmap_params")
    @patch("monitor_web.services.previsao.bq_locations_probe", return_value=True)
    @patch("monitor_web.services.bq_cache.get_previsao_heatmap_from_bq_cached")
    @patch("monitor_web.services.previsao.cached_call")
    def test_heatmap_payload_omits_verification(self, mock_cached, mock_bq, _probe, mock_params):
        mock_params.return_value = {
            "dataset": "efc_clima",
            "horizon_past": 10,
            "horizon_future": 25,
            "fc_km": 379,
        }
        import numpy as np

        kms = [100.0, 200.0]
        dates = ["2026-05-18", "2026-05-19"]
        z = np.array([[1.0, 2.0], [3.0, 4.0]])
        mock_bq.return_value = (kms, ["18/05", "19/05"], z, dates, {}, z, np.full_like(z, np.nan))
        mock_cached.side_effect = lambda _k, _ttl, fn, *a, **kw: fn(*a, **kw)
        out = build_previsao_heatmap_payload(self._req())
        self.assertNotIn("verification", out)
        self.assertTrue(out.get("available"))
        self.assertIsNotNone((out.get("charts") or {}).get("heatmap"))


class PrevisaoHeatmapTodayMarkerTests(SimpleTestCase):
    def test_xaxis_sparse_ticks_for_dense_km(self):
        kms = [float(x) for x in range(0, 878, 10)]
        dates_iso = ["2026-06-18", "2026-06-19"]
        z = np.array([[1.0] * len(kms), [2.0] * len(kms)])
        fig_dict, _ = _build_heatmap_fig(kms, ["18/06", "19/06"], z, dates_iso, {}, z, np.full_like(z, np.nan))
        xa = (fig_dict.get("layout") or {}).get("xaxis") or {}
        self.assertEqual(xa.get("tickmode"), "array")
        self.assertLessEqual(len(xa.get("tickvals") or []), 15)
        self.assertIsInstance((xa.get("tickvals") or [0])[0], int)

    def test_blend_obs_past_fills_missing_median(self):
        from monitor_web.services.previsao import _heatmap_blend_obs_past

        z = np.array([[np.nan, 1.0], [2.0, np.nan]])
        z_obs = np.array([[5.0, 1.0], [2.0, 8.0]])
        dates = ["2026-06-20", "2026-06-25"]
        with patch("monitor_web.services.previsao._heatmap_today_iso", return_value="2026-06-22"):
            out = _heatmap_blend_obs_past(z, z_obs, dates)
        self.assertAlmostEqual(out[0, 0], 5.0)
        self.assertTrue(np.isnan(out[1, 1]))

    def test_today_line_when_today_in_dates(self):
        today = _heatmap_today_iso()
        kms = [100.0, 200.0, 300.0]
        dates_iso = ["2026-01-01", today, "2026-12-31"]
        day_lbl = ["01/01", today[8:10] + "/" + today[5:7], "31/12"]
        z = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]])
        fig_dict, meta = _build_heatmap_fig(kms, day_lbl, z, dates_iso, {}, z, np.full_like(z, np.nan))
        self.assertIn("today_iso", meta)
        shapes = (fig_dict.get("layout") or {}).get("shapes") or []
        self.assertTrue(any(s.get("y0") == today and s.get("y1") == today for s in shapes))

    def test_obs_rain_dots_when_merge_above_threshold(self):
        kms = [100.0, 200.0]
        dates_iso = ["2026-05-18", "2026-05-19"]
        z = np.array([[1.0, 2.0], [3.0, 4.0]])
        z_obs = np.array([[2.0, 6.0], [12.0, 1.0]])
        fig_dict, meta = _build_heatmap_fig(kms, ["18/05", "19/05"], z, dates_iso, {}, z, z_obs)
        self.assertGreaterEqual(meta.get("rain_dots_n", 0), 2)
        scatter = [t for t in (fig_dict.get("data") or []) if t.get("type") == "scatter"]
        self.assertTrue(scatter)

    def test_no_today_line_when_today_absent(self):
        kms = [100.0]
        dates_iso = ["2020-01-01", "2020-01-02"]
        z = np.array([[1.0], [2.0]])
        fig_dict, meta = _build_heatmap_fig(kms, ["a", "b"], z, dates_iso, {}, z, np.full_like(z, np.nan))
        self.assertNotIn("today_iso", meta)
        shapes = (fig_dict.get("layout") or {}).get("shapes") or []
        self.assertEqual(len(shapes), 0)


class BoletimMergeDayWideRowTests(SimpleTestCase):
    def test_6h_steps_realistic_four_slots(self):
        row = pd.Series(
            {
                "mm_00": 2.5,
                "mm_06": 18.0,
                "mm_12": 31.0,
                "mm_18": 3.5,
                "precip_24h_mm": 55.0,
            }
        )
        out, kind = _boletim_merge_day_from_wide_row(row)
        self.assertEqual(kind, "6h")
        self.assertEqual(len(out), 4)
        self.assertEqual(list(out["label"]), ["00h", "06h", "12h", "18h"])
        self.assertAlmostEqual(float(out["precip_step_mm"].sum()), 55.0)
        self.assertAlmostEqual(float(out.iloc[2]["precip_step_mm"]), 31.0)

    def test_6h_steps_when_two_or_more_slots_present(self):
        row = pd.Series(
            {"mm_00": 10.0, "mm_06": 5.0, "mm_12": None, "mm_18": 2.0, "precip_24h_mm": 99.0}
        )
        out, kind = _boletim_merge_day_from_wide_row(row)
        self.assertEqual(kind, "6h")
        self.assertEqual(len(out), 4)
        self.assertAlmostEqual(float(out.iloc[0]["precip_step_mm"]), 10.0)

    def test_wide_row_two_slots_forces_6h_not_flat(self):
        row = pd.Series(
            {"mm_00": 48.0, "mm_06": 0.0, "mm_12": None, "mm_18": None, "precip_24h_mm": 48.0}
        )
        out, kind = _boletim_merge_day_from_wide_row(row)
        self.assertEqual(kind, "6h")
        self.assertAlmostEqual(float(out.iloc[0]["precip_step_mm"]), 48.0)

    def test_single_6h_matching_daily_keeps_6h_shape(self):
        row = pd.Series(
            {"mm_00": 48.0, "mm_06": None, "mm_12": None, "mm_18": None, "precip_24h_mm": 48.0}
        )
        out, kind = _boletim_merge_day_from_wide_row(row)
        self.assertEqual(kind, "6h")
        self.assertEqual(len(out), 4)
        self.assertEqual(list(out["label"]), ["00h", "06h", "12h", "18h"])
        self.assertAlmostEqual(float(out.iloc[0]["precip_step_mm"]), 48.0)

    def test_single_6h_only_00_without_daily_keeps_6h(self):
        rows = [{"variable": "precip_mm_6h_00", "mm": 52.0}]
        out, kind = _boletim_merge_day_from_var_rows(rows)
        self.assertEqual(kind, "6h")
        self.assertEqual(len(out), 4)
        self.assertAlmostEqual(float(out.iloc[0]["precip_step_mm"]), 52.0)

    def test_four_6h_slots_only_00_nonzero_keeps_6h_shape(self):
        rows = [
            {"variable": "precip_mm_6h_00", "mm": 40.0},
            {"variable": "precip_mm_6h_06", "mm": 0.0},
            {"variable": "precip_mm_6h_12", "mm": 0.0},
            {"variable": "precip_mm_6h_18", "mm": 0.0},
            {"variable": "precip_24h_mm", "mm": 40.0},
        ]
        out, kind = _boletim_merge_day_from_var_rows(rows)
        self.assertEqual(kind, "6h")
        self.assertAlmostEqual(float(out["precip_step_mm"].sum()), 40.0)
        self.assertAlmostEqual(float(out.iloc[0]["precip_step_mm"]), 40.0)
        self.assertAlmostEqual(float(out.iloc[1]["precip_step_mm"]), 0.0)

    def test_hourly_two_hours_not_equal_quarters(self):
        by_h = {0: 5.0, 8: 12.0, 16: 3.0}
        self.assertTrue(_boletim_hourly_distribution_usable(by_h))
        out = pd.DataFrame(
            [{"label": f"{h:02d}h", "precip_step_mm": float(by_h.get(h, 0.0))} for h in range(24)]
        )
        chart, kind, _ = _day_series_for_chart(out, "1h")
        self.assertEqual(kind, "1h")
        self.assertNotAlmostEqual(float(chart.iloc[0]["precip_step_mm"]), float(chart.iloc[8]["precip_step_mm"]))

    def test_single_6h_not_matching_daily_keeps_6h(self):
        row = pd.Series(
            {"mm_00": 10.0, "mm_06": None, "mm_12": None, "mm_18": None, "precip_24h_mm": 40.0}
        )
        out, kind = _boletim_merge_day_from_wide_row(row)
        self.assertEqual(kind, "6h")
        self.assertAlmostEqual(float(out.iloc[0]["precip_step_mm"]), 10.0)

    def test_24h_flat_without_6h_columns(self):
        row = pd.Series({"precip_24h_mm": 48.0})
        out, kind = _boletim_merge_day_from_wide_row(row)
        self.assertEqual(kind, "24h_flat")
        self.assertEqual(len(out), 4)
        self.assertAlmostEqual(float(out.iloc[0]["precip_step_mm"]), 12.0)

    def test_no_keyerror_on_missing_variable_column(self):
        row = pd.Series({"mm_00": None, "mm_06": None, "mm_12": None, "mm_18": None, "precip_24h_mm": 12.0})
        out, kind = _boletim_merge_day_from_wide_row(row)
        self.assertEqual(kind, "24h_flat")
        self.assertFalse(out.empty)

    def test_var_rows_canonicalizes_suffix_and_orders_6h(self):
        rows = [
            {"variable": "precip_mm_6h_6", "mm": 5.0},
            {"variable": "precip_mm_6h_12", "mm": 12.0},
            {"variable": "precip_mm_6h_0", "mm": 1.0},
            {"variable": "precip_mm_6h_18", "mm": 2.0},
        ]
        out, kind = _boletim_merge_day_from_var_rows(rows)
        self.assertEqual(kind, "6h")
        self.assertEqual(list(out["label"]), ["00h", "06h", "12h", "18h"])
        self.assertAlmostEqual(float(out.iloc[1]["precip_step_mm"]), 5.0)

    def test_single_6h_dominates_inconsistent_daily_keeps_6h(self):
        row = pd.Series(
            {"mm_00": 42.25, "mm_06": None, "mm_12": None, "mm_18": None, "precip_24h_mm": 1.28}
        )
        out, kind = _boletim_merge_day_from_wide_row(row)
        self.assertEqual(kind, "6h")
        self.assertAlmostEqual(float(out.iloc[0]["precip_step_mm"]), 42.25, places=4)

    def test_hourly_usable_rejects_single_midnight_hour(self):
        self.assertFalse(_boletim_hourly_distribution_usable({0: 50.0}))
        self.assertTrue(_boletim_hourly_distribution_usable({0: 1.0, 8: 2.0, 16: 3.0}))
        self.assertTrue(_boletim_hourly_distribution_usable({2: 1.0, 14: 4.0}))


class MergeDailyObsSeriesTests(SimpleTestCase):
    def test_merge_daily_sql_uses_table_alias_not_cte_name(self):
        expr = _merge_daily_mm_sql("mm", table_alias="s")
        self.assertIn("s.variable", expr)
        self.assertNotIn("per_var.variable", expr)

    def test_partial_6h_prefers_24h_when_single_slot(self):
        sql_logic = _merge_daily_mm_sql("mm", table_alias="s")
        self.assertIn("COUNTIF", sql_logic)
        self.assertIn(">= 2", sql_logic)

    @patch("gcp.bq_reader_extended._bq_to_dataframe")
    @patch("gcp.bq_reader_extended._dataset_tables")
    def test_merge_daily_obs_sql_prefers_hourly_sum(self, mock_tables, mock_df):
        import pandas as pd

        captured_sql = []

        class _Client:
            def query(self, sql, **_kw):
                captured_sql.append(sql)
                return object()

        mock_tables.return_value = (_Client(), "climaticsystem", "climaticsystem.efc_clima.clima_series", "loc")
        mock_df.return_value = pd.DataFrame({"date": [], "mm": []})
        merge_daily_obs_series_for_km_bq(379, past_days=10, future_days=5)
        sql_calls = [s for s in captured_sql if "hourly_daily" in s and "fallback_daily" in s]
        self.assertGreaterEqual(len(sql_calls), 1)
        sql = sql_calls[-1]
        self.assertIn("precip_1h_mm", sql)
        self.assertIn("valid_time IS NOT NULL", sql)
        self.assertIn("hourly_daily", sql)
        self.assertIn("fallback_daily", sql)
        self.assertIn("SUM(mm)", sql)
        self.assertIn("COALESCE(h.mm, f.mm)", sql)

    @patch("gcp.bq_reader_extended._bq_to_dataframe")
    @patch("gcp.bq_reader_extended._dataset_tables")
    def test_merge_daily_obs_returns_series_from_bq(self, mock_tables, mock_df):
        import pandas as pd

        class _Client:
            def query(self, *_a, **_kw):
                return object()

        mock_tables.return_value = (_Client(), "climaticsystem", "climaticsystem.efc_clima.clima_series", "loc")
        mock_df.return_value = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-05-18", "2026-05-19"]),
                "mm": [0.0, 0.62],
            }
        )
        ts, val = merge_daily_obs_series_for_km_bq(379, past_days=10, future_days=5)
        self.assertEqual(len(ts), 2)
        self.assertEqual(len(val), 2)


class CountSeriesExceedancesTests(SimpleTestCase):
    def test_empty_or_none_returns_zero(self):
        from gcp.bq_reader_clima import count_precip_24h_cells_above_for_date, count_series_exceedances

        self.assertEqual(count_series_exceedances(pd.DataFrame(), "precip_24h_mm", 49.0), 0)
        self.assertEqual(count_precip_24h_cells_above_for_date(None, "2026-05-20", 49.0), 0)

    def test_long_format_counts_above_threshold(self):
        from gcp.bq_reader_clima import count_series_exceedances

        df = pd.DataFrame(
            {
                "variable": ["precip_24h_mm", "precip_24h_mm", "precip_120h_mm"],
                "value": [50.0, 30.0, 300.0],
            }
        )
        self.assertEqual(count_series_exceedances(df, "precip_24h_mm", 49.0), 1)

    def test_missing_variable_column_no_keyerror(self):
        from gcp.bq_reader_clima import count_precip_24h_cells_above_for_date, count_series_exceedances

        df = pd.DataFrame({"date": ["2026-05-20"], "location_id": [1], "mm": [12.0]})
        self.assertEqual(count_series_exceedances(df, "precip_24h_mm", 49.0), 0)
        self.assertEqual(count_precip_24h_cells_above_for_date(df, "2026-05-20", 49.0), 0)

    def test_wide_format_precip_24h_mm(self):
        from gcp.bq_reader_clima import count_precip_24h_cells_above_for_date

        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-05-20", "2026-05-20", "2026-05-21"]),
                "precip_24h_mm": [55.0, 10.0, 60.0],
            }
        )
        self.assertEqual(count_precip_24h_cells_above_for_date(df, "2026-05-20", 49.0), 1)


class BoletimOccurrenceDaysTests(SimpleTestCase):
    def test_occurrence_days_from_daily_threshold(self):
        import pandas as pd

        daily = pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-03-20", "2025-03-21", "2025-03-22"]),
                "precip_24h_mm": [30.0, 55.0, 49.0],
            }
        )
        days = _occurrence_days_from_daily(daily, threshold=49.0)
        self.assertEqual([d["date"] for d in days], ["2025-03-21", "2025-03-22"])
        self.assertEqual(days[0]["chip_label"], "21/03 · 55 mm")

    def test_default_prefers_peak_when_exceedance(self):
        days = [{"date": "2025-03-21"}, {"date": "2025-03-22"}]
        self.assertEqual(_default_occurrence_date(days, "2025-03-22"), "2025-03-22")
        self.assertEqual(_default_occurrence_date(days, "2025-03-20"), "2025-03-21")

    def test_default_empty_occurrence_uses_peak(self):
        self.assertEqual(_default_occurrence_date([], "2025-04-01"), "2025-04-01")


class MergeEvGridTests(SimpleTestCase):
    def test_ev_follows_merge_pixel_not_10km_bin(self):
        from lib_merge_ev import ev_bounds_for_km, merge_ev_catalog

        cat = merge_ev_catalog()
        self.assertGreaterEqual(len(cat), 90)
        ev, start, end, kms = ev_bounds_for_km(408)
        self.assertEqual((ev, start, end), (46, 404, 415))
        self.assertNotEqual((start, end), (400, 409))
        self.assertEqual(set(kms), set(range(404, 416)))
        self.assertEqual(ev_bounds_for_km(404)[0], ev)
        self.assertEqual(ev_bounds_for_km(415)[0], ev)


class AnnualExceedanceFrequencyBqTests(SimpleTestCase):
    @patch("gcp.bq_reader_extended._bq_to_dataframe")
    @patch("gcp.bq_reader_extended._dataset_tables")
    def test_uses_bq_to_dataframe_not_storage_api_only(self, mock_tables, mock_to_df):
        class _Job:
            pass

        class _Client:
            project = "climaticsystem"

            def query(self, _sql, job_config=None):
                return _Job()

        mock_tables.return_value = (_Client(), "climaticsystem", "p.d.clima_series", "p.d.locations")
        mock_to_df.return_value = pd.DataFrame(
            {"km_efc": [10.0], "exceed_days_24": [2], "max_24h_mm": [55.0]}
        )
        from gcp.bq_reader_clima import annual_exceedance_frequency_by_km_bq

        df = annual_exceedance_frequency_by_km_bq(2026, dataset="efc_clima", source="merge")
        self.assertEqual(len(df), 1)
        self.assertTrue(mock_to_df.called)


class BoletimBqCacheTests(SimpleTestCase):
    def test_boletim_cache_key_includes_year_and_fingerprint_suffix(self):
        key = boletim_cache_key("boletim_freq", "efc_clima", 2026, "")
        self.assertTrue(key.startswith("boletim_freq_efc_clima_2026_"))
        self.assertIn("boletim_day", boletim_cache_key("boletim_day", "efc_clima", "2026-03-21", 1, 10))

    def test_daily_and_day_keys_differ(self):
        daily = boletim_cache_key("boletim_daily", "efc_clima", 2026, 1, 10, "")
        day = boletim_cache_key("boletim_day", "efc_clima", "2026-03-21", 1, 10, "")
        self.assertNotEqual(daily, day)

    def test_comparison_cache_key_distinct_from_daily(self):
        comp = boletim_cache_key("boletim_comparison", "efc_clima", 2026, 1, 10, "")
        daily = boletim_cache_key("boletim_daily", "efc_clima", 2026, 1, 10, "")
        self.assertNotEqual(comp, daily)
        self.assertIn("boletim_comparison", comp)


class BoletimRaijinComparisonTests(SimpleTestCase):
    def test_effective_verify_mm(self):
        from gcp.bq_raijin import effective_verify_mm

        self.assertEqual(effective_verify_mm(30.0, None), 30.0)
        self.assertEqual(effective_verify_mm(None, 55.0), 55.0)
        self.assertEqual(effective_verify_mm(30.0, 55.0), 55.0)
        self.assertIsNone(effective_verify_mm(None, None))

    
    def test_station_catalog_includes_ews_network_field(self):
        """Catálogo de estações expõe network e location_id ews_* para associação km."""
        import pandas as pd
        from unittest import mock
        from gcp import bq_reader_extended as bqx

        fake = pd.DataFrame(
            [
                {"codEstacao": "EWS6737", "latitude": -5.16, "longitude": -48.26, "nome": "Abrigo km 629"},
            ]
        )
        with mock.patch("lib_efc.load_orion_estacoes", return_value=fake):
            # Avoid depending on local CSV presence: empty CSV path → still merges Orion.
            with mock.patch.object(bqx, "_clean_station_display_name", side_effect=lambda n, c: n or c):
                cat = bqx._station_catalog_from_csv()
        self.assertFalse(cat.empty)
        ews = cat[cat["location_id"].astype(str).str.startswith("ews_")]
        self.assertFalse(ews.empty)
        self.assertTrue((ews["network"] == "ews").all())

    def test_load_monitor_daily_exceed_merges_ews_sources_query(self):
        from gcp import bq_monitor_daily as md
        self.assertIn("include_ews", md.load_monitor_daily_exceed_df.__code__.co_varnames)


    def test_ews_verify_band_near_alert_km(self):
        import pandas as pd

        from gcp.bq_raijin import _attach_verify_mm_near_alert_km

        alerts = pd.DataFrame({"day": ["2026-05-15"], "km_efc": [650], "pred": [1]})
        ews = pd.DataFrame(
            {
                "day": ["2026-05-15", "2026-05-15"],
                "km_efc": [650, 663],
                "ews_mm": [10.0, 49.6],
            }
        )
        out = _attach_verify_mm_near_alert_km(alerts, None, ews, band_km=20)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out.iloc[0]["ews_mm"]), 49.6)
        self.assertAlmostEqual(float(out.iloc[0]["verify_mm"]), 49.6)

    def test_feedback_annotate_fp_tp_weights(self):
        from gcp.raijin_feedback import annotate_feedback_train_columns

        df = pd.DataFrame({"outcome": ["tp", "fp", "pending"]})
        out = annotate_feedback_train_columns(df)
        self.assertEqual(int(out.loc[0, "label"]), 1)
        self.assertEqual(int(out.loc[1, "label"]), 0)
        self.assertEqual(int(out.loc[1, "hard_negative"]), 1)
        self.assertGreater(float(out.loc[1, "sample_weight"]), float(out.loc[0, "sample_weight"]))

    def test_strict_svm_threshold_floor(self):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "raijin_weekly_retrain",
            Path(__file__).resolve().parents[1] / "gcp" / "raijin_weekly_retrain.py",
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        self.assertEqual(mod.strict_svm_threshold_floor(-0.64, -0.455), -0.455)
        self.assertEqual(mod.strict_svm_threshold_floor(-0.40, -0.455), -0.40)

    def test_promotion_blocks_alert_increase(self):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "raijin_weekly_retrain",
            Path(__file__).resolve().parents[1] / "gcp" / "raijin_weekly_retrain.py",
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        class _Fake:
            pass

        fake_dir = Path("/nonexistent/model")
        mod.count_holdout_alerts = lambda *a, **k: 120  # noqa: ARG005
        mod.holdout_promotion_baseline = lambda **k: (100, -0.455)  # noqa: ARG005
        out = mod.evaluate_promotion(
            fake_dir,
            prod_dir=fake_dir,
            features_csv=fake_dir,
            holdout_start="2026-05-01",
            holdout_end="2026-06-15",
            state_path=fake_dir,
        )
        self.assertFalse(out["promote_ok"])
        self.assertIn(">", out["promote_reason"] or "")

    def test_outcome_from_station_mm(self):
        from gcp.bq_raijin import outcome_from_station_mm

        self.assertEqual(outcome_from_station_mm(1, 55.0), "tp")
        self.assertEqual(outcome_from_station_mm(1, 10.0), "fp")
        self.assertEqual(outcome_from_station_mm(0, 55.0), "fn")
        self.assertEqual(outcome_from_station_mm(0, 10.0), "tn")
        self.assertEqual(outcome_from_station_mm(1, None), "pending")

    def test_station_vs_alert_outcome_tp_fp_fn(self):
        self.assertEqual(station_vs_alert_outcome(True, True), "tp")
        self.assertEqual(station_vs_alert_outcome(True, False), "fp")
        self.assertEqual(station_vs_alert_outcome(False, True), "fn")
        self.assertEqual(station_vs_alert_outcome(False, False), "tn")
        self.assertEqual(station_vs_alert_outcome(True, None), "")

    def test_raijin_mm_display_nominal_50(self):
        self.assertEqual(raijin_mm_display_value(True), RAIJIN_BOLETIM_NOMINAL_MM)
        self.assertEqual(RAIJIN_BOLETIM_NOMINAL_MM, 50.0)
        self.assertIsNone(raijin_mm_display_value(False))

    def test_build_comparison_df_join_and_outcomes(self):
        merge = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-03", "2026-04-04"]),
                "precip_24h_mm": [30.0, 40.0],
            }
        )
        with (
            patch("gcp.bq_raijin.get_raijin_boletim_ev_days_bq") as mock_rj,
            patch("gcp.bq_raijin.get_station_max_mm_ev_bq") as mock_st,
        ):
            mock_rj.return_value = pd.DataFrame(
                {
                    "date": ["2026-04-03", "2026-04-04"],
                    "raijin_alert": [1, 1],
                    "n_alert_km": [2, 1],
                    "raijin_model_outcome": ["tp", "fp"],
                }
            )
            mock_st.return_value = pd.DataFrame(
                {
                    "date": ["2026-04-03", "2026-04-04"],
                    "station_max_mm": [55.0, 20.0],
                }
            )
            df = build_boletim_ev_comparison_df(
                dataset="efc_clima",
                year=2026,
                km_start=1,
                km_end=10,
                merge_daily_df=merge,
            )
        row_tp = df.loc[df["date"] == "2026-04-03"].iloc[0]
        row_fp = df.loc[df["date"] == "2026-04-04"].iloc[0]
        self.assertTrue(row_tp["raijin_alert"])
        self.assertEqual(row_tp["raijin_mm_display"], 50.0)
        self.assertEqual(row_tp["raijin_outcome"], "tp")
        self.assertEqual(row_fp["raijin_outcome"], "fp")
        self.assertFalse(row_fp["station_event"])

    def test_comparison_summary_pod_far(self):
        df = pd.DataFrame(
            {
                "station_max_mm": [55.0, 20.0, 60.0],
                "raijin_alert": [True, True, False],
                "merge_event": [False, False, True],
                "station_event": [True, False, True],
            }
        )
        summary = build_boletim_comparison_summary(df)
        self.assertEqual(summary["raijin"]["hits"], 1)
        self.assertEqual(summary["raijin"]["false_alarms"], 1)
        self.assertEqual(summary["raijin"]["misses"], 1)
        self.assertAlmostEqual(summary["raijin"]["pod"], 1 / 2)
        self.assertAlmostEqual(summary["raijin"]["far"], 0.5)
        self.assertIn("Raijin vs estações", summary["summary_line"])

    def test_comparison_table_rows_event_filter(self):
        df = pd.DataFrame(
            {
                "date": ["2026-04-03", "2026-04-04", "2026-04-05"],
                "merge_mm": [30.0, 40.0, 55.0],
                "merge_event": [False, False, True],
                "raijin_alert": [True, False, False],
                "raijin_outcome": ["tp", "", ""],
                "station_max_mm": [55.0, np.nan, 30.0],
                "station_event": [True, False, False],
            }
        )
        rows = _comparison_table_rows(df)
        dates = {r["date"] for r in rows}
        self.assertIn("2026-04-03", dates)
        self.assertIn("2026-04-05", dates)
        self.assertNotIn("2026-04-04", dates)
        tp_row = next(r for r in rows if r["date"] == "2026-04-03")
        self.assertEqual(tp_row["raijin_mm"], "50")
        self.assertEqual(tp_row["result"], "TP")


class BqSeriesExceedanceCountTests(SimpleTestCase):
    def test_empty_or_missing_columns_returns_zero(self):
        from gcp.bq_reader_clima import count_precip_24h_cells_above_for_date, count_series_exceedances

        self.assertEqual(count_series_exceedances(pd.DataFrame()), 0)
        self.assertEqual(count_precip_24h_cells_above_for_date(None, "2026-05-20"), 0)
        self.assertEqual(count_precip_24h_cells_above_for_date(pd.DataFrame(), "2026-05-20"), 0)
        self.assertEqual(
            count_precip_24h_cells_above_for_date(pd.DataFrame({"km": [1]}), "2026-05-20"),
            0,
        )

    def test_long_schema_counts_exceedances_for_date(self):
        from gcp.bq_reader_clima import count_precip_24h_cells_above_for_date, count_series_exceedances

        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-05-19", "2026-05-20", "2026-05-20", "2026-05-20"]),
                "variable": ["precip_24h_mm", "precip_24h_mm", "precip_120h_mm", "precip_24h_mm"],
                "value": [60.0, 30.0, 300.0, 55.0],
            }
        )
        self.assertEqual(count_series_exceedances(df[df["date"].dt.strftime("%Y-%m-%d") == "2026-05-20"]), 1)
        self.assertEqual(count_precip_24h_cells_above_for_date(df, "2026-05-20", 49.0), 1)

    def test_wide_schema_precip_column(self):
        from gcp.bq_reader_clima import count_series_exceedances

        df = pd.DataFrame({"precip_24h_mm": [30.0, 50.0, 49.0]})
        self.assertEqual(count_series_exceedances(df, threshold_mm=49.0), 1)


class BqStatusMessageTests(SimpleTestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_warning_when_use_bq_off(self):
        msg = bq_warning_message(lang_pt=True)
        self.assertIn("USE_BQ", msg)

    def test_probe_failed_message(self):
        msg = bq_warning_message(lang_pt=True, probe_failed=True)
        self.assertIn("locations", msg.lower())


class HomeMapFragmentTests(SimpleTestCase):
    def _req(self, query: str = ""):
        class R:
            GET = QueryDict(query)
            session = {}

        return R()

    @patch("monitor_web.services.home_fragments.bq_locations_probe", return_value=False)
    def test_map_fragment_without_bq_shows_error(self, _probe):
        from monitor_web.services.home_fragments import build_map_fragment

        ctx = build_map_fragment(self._req("date=2026-05-20&accum=120"))
        self.assertIn("map_error", ctx)
        self.assertTrue(ctx["map_error"])
        self.assertFalse(ctx.get("map_embed_url"))

    @patch("monitor_web.services.home_fragments.bq_locations_probe", return_value=True)
    @patch("monitor_web.services.home_fragments._clamp_date_to_bq_range")
    def test_map_fragment_with_bq_returns_embed_url(self, mock_clamp, _probe):
        from monitor_web.services.home_fragments import build_map_fragment

        mock_clamp.return_value = (date(2026, 5, 20), None, None)
        ctx = build_map_fragment(self._req("date=2026-05-20&accum=120"))
        self.assertIn("map_embed_url", ctx)
        self.assertIn("map-embed", ctx["map_embed_url"])

    @patch("monitor_web.services.home_fragments.MAP_BUILD_TIMEOUT_SEC", 1)
    @patch("monitor_web.services.home_fragments._map_html_for_day_uncached")
    def test_map_embed_timeout_returns_message(self, mock_uncached):
        import time

        from monitor_web.services.home_fragments import _map_html_for_day

        def slow(*_a, **_kw):
            time.sleep(3)
            return "<html></html>", None

        mock_uncached.side_effect = slow
        html, err = _map_html_for_day("2026-05-20", "efc_clima", 120, True, embed_document=True)
        self.assertEqual(html, "")
        self.assertIn("demorou", err)


class MapEmbedAccAlertsBridgeTests(SimpleTestCase):
    def test_standalone_map_html_includes_acc_alerts_bridge(self):
        from monitor_web.services.home import folium_map_standalone_html

        class _Root:
            width = ""
            height = ""

            def render(self):
                return "<html><body></body></html>"

        class _Map:
            _efc_station_legend_html = ""

            def get_root(self):
                return _Root()

        html = folium_map_standalone_html(_Map(), height="390px")
        self.assertIn("efc-acc-alerts", html)
        self.assertIn("efc-map-ready", html)
        self.assertIn("max-height:min(42vh,220px)", html)
        self.assertIn("@media (max-width:480px)", html)


class HomeChartsForecastDailyMergeTests(SimpleTestCase):
    @patch("monitor_web.services.home_fragments.bq_locations_probe", return_value=True)
    @patch("gcp.bq_reader_clima.get_model_pr_agreement_railway_heatmap_from_bq")
    @patch("gcp.bq_reader_clima.merge_daily_obs_series_for_km_bq")
    @patch("gcp.bq_reader_clima.merge_6h_series_for_km_bq")
    @patch("gcp.bq_reader_clima.forecast_comparison_series_bq")
    @patch("gcp.bq_reader_clima.km_exceedance_count_timeseries_bq")
    def test_forecast_chart_uses_merge_daily_obs(
        self,
        mock_km_ex,
        mock_fc,
        mock_m6,
        mock_md,
        mock_heat,
        _probe,
    ):
        from monitor_web.services.home_fragments import _build_charts_payload_uncached

        d0 = pd.Timestamp("2026-05-10").normalize()
        mock_km_ex.return_value = ([], [], [])
        mock_fc.return_value = {
            "ecmwf": ([d0], [12.0]),
            "gfs": ([], []),
            "icon": ([], []),
            "weathernext2": ([], []),
        }
        mock_m6.return_value = ([d0], [3.0])
        mock_md.return_value = ([d0], [18.5])
        mock_heat.return_value = (None, None, np.array([]), [], None, None)

        out = _build_charts_payload_uncached(
            {
                "dataset": "efc_clima",
                "selected_date": "2026-05-20",
                "accum_hours": 120,
                "fc_km": 379,
                "km_max": 900,
            }
        )
        fig = out["charts"]["forecast"]
        self.assertIsNotNone(fig)
        names = [t.get("name") for t in fig.get("data", [])]
        self.assertIn("MERGE (observado)", names)
        merge_traces = [t for t in fig["data"] if t.get("name") == "MERGE (observado)"]
        self.assertEqual(merge_traces[0].get("yaxis"), "y")
        mock_md.assert_called_once()


class BqLocationsProbeTests(SimpleTestCase):
    @patch("monitor_web.services.home_fragments.cached_call")
    def test_probe_true_when_query_returns_row(self, mock_cached):
        mock_cached.side_effect = lambda _k, _ttl, fn: fn()

        class _Row:
            def __getitem__(self, key):
                return 1

        class _Job:
            def result(self):
                return [_Row()]

        class _Client:
            project = "climaticsystem"

            def query(self, _sql):
                return _Job()

        with patch("google.cloud.bigquery.Client", return_value=_Client()):
            from monitor_web.services.home_fragments import bq_locations_probe

            self.assertTrue(bq_locations_probe("efc_clima"))

    @patch("monitor_web.services.home_fragments.cached_call")
    def test_probe_false_when_query_empty(self, mock_cached):
        mock_cached.side_effect = lambda _k, _ttl, fn: fn()

        class _Job:
            def result(self):
                return []

        class _Client:
            project = "climaticsystem"

            def query(self, _sql):
                return _Job()

        with patch("google.cloud.bigquery.Client", return_value=_Client()):
            from monitor_web.services.home_fragments import bq_locations_probe

            self.assertFalse(bq_locations_probe("efc_clima"))


class BoletimDiaDistribuicaoApiTests(SimpleTestCase):
    def _req(self, query: str):
        class R:
            GET = QueryDict(query)

        return R()

    def test_missing_date(self):
        payload = build_boletim_dia_distribuicao_payload(self._req("km_start=1&km_end=10"))
        self.assertFalse(payload["available"])
        self.assertIn("date", payload["error"].lower())


class RailwayVerificationAggregateTests(SimpleTestCase):
    def test_pools_km_day_pairs(self):
        today = date.today()
        d1 = (today - __import__("datetime").timedelta(days=2)).isoformat()
        d2 = (today - __import__("datetime").timedelta(days=1)).isoformat()
        rows = []
        for km in (0, 10):
            for day in (d1, d2):
                rows.append({"day": day, "km_efc": km, "source": "obs", "mm": 50.0})
                rows.append({"day": day, "km_efc": km, "source": "ecmwf", "mm": 55.0})
        df = pd.DataFrame(rows)
        df["day"] = pd.to_datetime(df["day"])
        out = _verification_metrics_from_long_df(
            df,
            {"obs_label": "MERGE (ferrovia)"},
            past_days=10,
            future_days=0,
            threshold_mm=5.0,
            pair_index_cols=("day", "km_efc"),
        )
        self.assertTrue(out["available"])
        self.assertEqual(out["threshold_mm"], 5.0)
        ecmwf = next(m for m in out["models"] if m["source"] == "ecmwf")
        self.assertEqual(ecmwf["n_pairs"], 4)
        self.assertEqual(out.get("n_verify_days"), 2)
        self.assertAlmostEqual(ecmwf["pod"], 1.0)


class VerificationCalendarPeriodTests(SimpleTestCase):
    def test_calendar_range_ytd_caps_at_today(self):
        start, end = verification_calendar_date_range(2026)
        self.assertEqual(start, date(2026, 1, 1))
        self.assertLessEqual(end, date(2026, 12, 31))
        self.assertLessEqual(end, date.today())

    def test_periods_split_annual_and_months(self):
        y = date.today().year
        rows = []
        for day in (f"{y}-01-15", f"{y}-02-10"):
            rows.append({"day": day, "km_efc": 0, "source": "obs", "mm": 12.0})
            rows.append({"day": day, "km_efc": 0, "source": "ecmwf", "mm": 14.0})
        df = pd.DataFrame(rows)
        df["day"] = pd.to_datetime(df["day"])
        out = _verification_periods_from_long_df(
            df,
            {"obs_label": "MERGE (ferrovia)", "railway_wide": True},
            year=y,
            threshold_mm=5.0,
            pair_index_cols=("day", "km_efc"),
        )
        self.assertTrue(out.get("available"))
        self.assertIn("annual", out)
        self.assertGreaterEqual(len(out.get("months") or []), 1)
        annual = out["annual"]
        self.assertTrue(annual.get("available"))
        ecmwf = next(m for m in annual["models"] if m["source"] == "ecmwf")
        self.assertEqual(ecmwf["n_pairs"], 2)
        self.assertEqual(annual.get("n_verify_days"), 2)

    def test_metric_help_is_short_portuguese(self):
        for key in ("roc_auc", "pbias", "pod", "far"):
            text = _METRIC_HELP_PT[key]
            self.assertLess(len(text), 120)
            self.assertNotIn("binário", text.lower())
        self.assertIn("amostra", _METRIC_HELP_PT["n_pairs"].lower())
        self.assertNotIn("km·dia", _METRIC_HELP_PT["n_pairs"])


class PrevisaoVerificationPayloadTests(SimpleTestCase):
    def test_payload_shape_from_period_raw(self):
        raw = {
            "obs_source": "merge",
            "obs_label": "MERGE (ferrovia)",
            "railway_wide": True,
            "km_step": 5,
            "verification_year": 2026,
            "verify_date_start": "2026-01-01",
            "verify_date_end": "2026-05-19",
            "available": True,
            "annual": {
                "period": "2026",
                "period_label": "2026 (acumulado)",
                "verify_date_start": "2026-01-01",
                "verify_date_end": "2026-05-19",
                "available": True,
                "models": [
                    {
                        "source": "ecmwf",
                        "label": "ECMWF",
                        "n_pairs": 10,
                        "roc_auc": 0.8,
                        "pbias": 5.0,
                        "pod": 0.7,
                        "far": 0.2,
                        "hits": 1,
                        "misses": 0,
                        "false_alarms": 0,
                    }
                ],
            },
            "months": [
                {
                    "period": "2026-05",
                    "period_label": "Maio 2026",
                    "verify_date_start": "2026-05-01",
                    "verify_date_end": "2026-05-19",
                    "available": True,
                    "models": [
                        {
                            "source": "ecmwf",
                            "label": "ECMWF",
                            "n_pairs": 3,
                            "roc_auc": 0.75,
                            "pbias": 2.0,
                            "pod": 0.6,
                            "far": 0.3,
                            "hits": 1,
                            "misses": 0,
                            "false_alarms": 0,
                        }
                    ],
                }
            ],
        }

        def fake_fetch(_p, *, obs_source, railway_wide, cache_only=False):
            r = dict(raw)
            r["obs_source"] = obs_source
            r["obs_label"] = "MERGE" if obs_source == "merge" else "BNDMET"
            return r

        from unittest.mock import patch

        p = {
            "dataset": "efc_clima",
            "horizon_past": 10,
            "horizon_future": 10,
            "obs_source": "merge",
            "fc_km": 100,
        }
        with patch(
            "monitor_web.services.previsao._fetch_verification_raw",
            side_effect=fake_fetch,
        ):
            out = _verification_payload_from_bq(p, railway_wide=True)
        self.assertTrue(out["available"])
        self.assertEqual(out["verification_year"], 2026)
        self.assertIsNotNone(out.get("annual"))
        self.assertGreaterEqual(len(out.get("months") or []), 1)
        self.assertIn("roc_auc", out["metric_help"])
        from monitor_web.services.previsao import _inclusive_calendar_days

        cmp0 = (out.get("comparisons") or [{}])[0]
        self.assertEqual(
            cmp0.get("n_verify_days"),
            _inclusive_calendar_days("2026-01-01", "2026-05-19"),
        )

    def test_km_verification_payload_includes_formatted_metrics(self):
        from unittest.mock import patch

        from monitor_web.services.previsao import _verification_payload_from_bq

        raw = {
            "obs_source": "merge",
            "obs_label": "MERGE (km ferroviário)",
            "railway_wide": False,
            "fc_km": 379,
            "verify_date_start": "2026-05-09",
            "verify_date_end": "2026-05-19",
            "available": True,
            "models": [
                {
                    "source": "ecmwf",
                    "label": "ECMWF",
                    "n_pairs": 6,
                    "roc_auc": 0.81,
                    "pbias": 4.5,
                    "pod": 0.7,
                    "far": 0.2,
                    "hits": 2,
                    "misses": 1,
                    "false_alarms": 0,
                }
            ],
        }
        p = {
            "dataset": "efc_clima",
            "horizon_past": 10,
            "horizon_future": 10,
            "obs_source": "merge",
            "fc_km": 379,
        }

        def fake_fetch(_p, *, obs_source, railway_wide, cache_only=False):
            self.assertFalse(railway_wide)
            return dict(raw, obs_source=obs_source)

        with patch(
            "monitor_web.services.previsao._fetch_verification_raw",
            side_effect=fake_fetch,
        ):
            out = _verification_payload_from_bq(p, railway_wide=False)
        self.assertTrue(out["available"])
        self.assertFalse(out["railway_wide"])
        ecmwf = out["models"][0]
        self.assertEqual(ecmwf["n_pairs"], 6)
        self.assertEqual(ecmwf["roc_auc"], "0.81")
        self.assertEqual(ecmwf["pbias_display"], "4.50%")
        self.assertEqual(ecmwf["pod"], "70%")
        self.assertEqual(ecmwf["far"], "20%")


class ForecastEnvelopeTests(SimpleTestCase):
    def test_min_max_across_all_models_per_day(self):
        import pandas as pd

        d0 = pd.Timestamp("2026-05-10").normalize()
        d1 = pd.Timestamp("2026-05-11").normalize()
        fc = {
            "ecmwf": ([d0, d1], [10.0, 5.0]),
            "gfs": ([d0], [30.0]),
            "icon": ([d0, d1], [20.0, 40.0]),
            "weathernext2": ([d1], [15.0]),
        }
        dates, lo, hi = _forecast_envelope_by_day(fc)
        self.assertEqual(dates, [d0, d1])
        self.assertEqual(lo, [10.0, 5.0])
        self.assertEqual(hi, [30.0, 40.0])

    def test_weathernext_alias_merged_into_envelope(self):
        import pandas as pd

        d0 = pd.Timestamp("2026-05-12").normalize()
        fc = {
            "ecmwf": ([d0], [1.0]),
            "gfs": ([], []),
            "icon": ([], []),
            "weathernext2": ([], []),
            "weathernext": ([d0], [99.0]),
        }
        _, lo, hi = _forecast_envelope_by_day(fc)
        self.assertEqual(lo, [1.0])
        self.assertEqual(hi, [99.0])

    def test_weathernext_alias_for_chart_series(self):
        import pandas as pd

        d0 = pd.Timestamp("2026-05-12").normalize()
        fc = {"weathernext2": ([], []), "weathernext": ([d0], [42.0])}
        dx, dy = _forecast_series_for_model(fc, "weathernext2")
        self.assertEqual(dx, [d0])
        self.assertEqual(dy, [42.0])


class BuildForecastFigTests(SimpleTestCase):
    def test_includes_merge_obs_trace_on_primary_axis(self):
        d0 = pd.Timestamp("2026-05-10").normalize()
        fc = {
            "ecmwf": ([d0], [12.0]),
            "gfs": ([], []),
            "icon": ([], []),
            "weathernext2": ([], []),
        }
        fig_dict = _build_forecast_fig(
            379,
            fc,
            [],
            [],
            [d0],
            [18.5],
            past_days=10,
            future_days=10,
            date_start="2026-05-01",
            date_end="2026-05-20",
        )
        self.assertIsNotNone(fig_dict)
        names = [t.get("name") for t in fig_dict.get("data", [])]
        self.assertIn("MERGE (observado)", names)
        merge_traces = [t for t in fig_dict["data"] if t.get("name") == "MERGE (observado)"]
        self.assertEqual(merge_traces[0].get("mode"), "lines")
        self.assertFalse(any(t.get("type") == "bar" for t in fig_dict.get("data", [])))


class PrevisaoVerificationCacheTests(SimpleTestCase):
    def test_cache_roundtrip_preserves_metrics(self):
        from datetime import datetime, timezone

        from gcp.previsao_verification_cache import cache_rows_to_ytd_raw, ytd_raw_to_cache_rows

        raw = {
            "obs_source": "merge",
            "obs_label": "MERGE (ferrovia)",
            "railway_wide": True,
            "km_step": 5,
            "verification_year": 2026,
            "verify_date_start": "2026-01-01",
            "verify_date_end": "2026-05-19",
            "available": True,
            "annual": {
                "period": "2026",
                "period_label": "2026 (acumulado)",
                "verify_date_start": "2026-01-01",
                "verify_date_end": "2026-05-19",
                "n_obs_points": 100,
                "models": [
                    {
                        "source": "ecmwf",
                        "label": "ECMWF",
                        "n_pairs": 10,
                        "roc_auc": 0.81,
                        "pbias": 4.5,
                        "pod": 0.7,
                        "far": 0.2,
                        "hits": 3,
                        "misses": 1,
                        "false_alarms": 1,
                    }
                ],
            },
            "months": [
                {
                    "period": "2026-05",
                    "period_label": "Maio 2026",
                    "verify_date_start": "2026-05-01",
                    "verify_date_end": "2026-05-19",
                    "n_obs_points": 20,
                    "models": [
                        {
                            "source": "gfs",
                            "label": "GFS",
                            "n_pairs": 4,
                            "roc_auc": 0.6,
                            "pbias": -2.0,
                            "pod": 0.5,
                            "far": 0.4,
                            "hits": 1,
                            "misses": 0,
                            "false_alarms": 0,
                        }
                    ],
                }
            ],
        }
        ts = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        rows = ytd_raw_to_cache_rows(raw, year=2026, threshold_mm=5.0, km_step=5, computed_at=ts)
        self.assertEqual(len(rows), 2)
        df = pd.DataFrame(rows)
        rebuilt = cache_rows_to_ytd_raw(df, "merge")
        self.assertIsNotNone(rebuilt)
        ecmwf = next(m for m in rebuilt["annual"]["models"] if m["source"] == "ecmwf")
        self.assertEqual(ecmwf["n_pairs"], 10)
        self.assertAlmostEqual(ecmwf["roc_auc"], 0.81)
        gfs = next(m for m in rebuilt["months"][0]["models"] if m["source"] == "gfs")
        self.assertEqual(gfs["n_pairs"], 4)

    def test_fetch_verification_prefers_cache(self):
        from unittest.mock import patch

        cached_raw = {
            "obs_source": "merge",
            "obs_label": "MERGE (ferrovia)",
            "railway_wide": True,
            "verification_year": 2026,
            "annual": {"available": True, "models": [{"source": "ecmwf", "n_pairs": 1}]},
            "months": [],
        }
        p = {"dataset": "efc_clima", "horizon_past": 10, "horizon_future": 10}
        with patch(
            "gcp.previsao_verification_cache.railway_forecast_verification_whole_railway_ytd_cached_bq",
            return_value=cached_raw,
        ) as mock_cached:
            from monitor_web.services.previsao import _fetch_verification_raw

            out = _fetch_verification_raw(p, obs_source="merge", railway_wide=True)
        mock_cached.assert_called_once()
        self.assertEqual(out["obs_source"], "merge")
        self.assertTrue(out["annual"]["available"])

    def test_cache_only_fetch_uses_from_cache_bq(self):
        from unittest.mock import patch

        cached_raw = {
            "obs_source": "merge",
            "annual": {"available": True, "models": [{"source": "ecmwf", "n_pairs": 1}]},
            "months": [],
        }
        p = {"dataset": "efc_clima", "horizon_past": 10, "horizon_future": 10}
        with patch(
            "gcp.previsao_verification_cache.railway_forecast_verification_whole_railway_ytd_from_cache_bq",
            return_value=cached_raw,
        ) as mock_from_cache:
            from monitor_web.services.previsao import _fetch_verification_raw

            _fetch_verification_raw(
                p, obs_source="merge", railway_wide=True, cache_only=True
            )
        mock_from_cache.assert_called_once()

    def test_verification_cache_miss_returns_precompute_hint(self):
        from unittest.mock import patch

        p = {"dataset": "efc_clima", "horizon_past": 10, "horizon_future": 10}
        with patch(
            "gcp.previsao_verification_cache.read_verification_cache_df",
            return_value=None,
        ):
            out = _verification_payload_from_cache_table(p)
        self.assertTrue(out["cache_miss"])
        self.assertIn("precompute_previsao_verification.py", out["precompute_hint"])
        self.assertIn("verification_cache", out["error"])
        self.assertFalse(out["available"])

    def test_precompute_hint_includes_dataset(self):
        hint = _previsao_verify_precompute_hint("efc_clima")
        self.assertNotIn("efc_clima", hint)
        self.assertIn("verification_cache", hint)
        self.assertIn("precompute_previsao_verification.py", hint)

    def test_verification_cache_fallback_env(self):
        from unittest.mock import patch

        from gcp.previsao_verification_cache import verification_cache_fallback_live

        with patch.dict("os.environ", {"PREVISAO_VERIFICATION_CACHE_FALLBACK": "0"}):
            self.assertFalse(verification_cache_fallback_live())
        with patch.dict("os.environ", {"PREVISAO_VERIFICATION_CACHE_FALLBACK": "1"}):
            self.assertTrue(verification_cache_fallback_live())


class IngestionStatusContextTests(SimpleTestCase):
    @patch("monitor_web.services.sidebar.get_last_ana_date", return_value="2026-05-19")
    @patch("monitor_web.services.sidebar.get_last_bndmet_date", return_value="2026-05-19")
    @patch("monitor_web.services.sidebar.get_last_era5_date", return_value="2026-05-19")
    @patch("monitor_web.services.sidebar.get_merge_info", return_value=("2026-05-19", None))
    @patch("gcp.bq_reader_clima.get_previsao_ingestion_summary_bq")
    @patch("gcp.bq_reader_clima.get_last_ingestion_dates_by_source_bq")
    def test_tooltip_lines_succinct_without_models(
        self,
        mock_by_src,
        mock_meta,
        *_local_dates,
    ):
        from monitor_web.services.sidebar import ingestion_status_context

        mock_by_src.return_value = {
            "ecmwf": {
                "ingested_at": "2026-05-20 08:00",
                "last_observation": "2026-05-19",
                "forecast_horizon": "2026-05-30",
            },
            "gfs": {"ingested_at": "2026-05-20 07:00", "forecast_horizon": "2026-05-28"},
            "merge": {"last_observation": "2026-05-19"},
        }
        mock_meta.return_value = {
            "ecmwf_last_ingest_str": "2026-05-20 08:00",
            "gfs_last_ingest_str": "2026-05-20 07:00",
            "next_pipeline_local_str": "21-05-2026 09:00",
        }
        ctx = ingestion_status_context("efc_clima")
        lines = ctx["tooltip_lines"]
        self.assertLessEqual(len(lines), 4)
        self.assertTrue(any(line.startswith("Última atualização:") for line in lines))
        self.assertTrue(any(line.startswith("Situação:") for line in lines))
        self.assertFalse(any("Sobre" in line for line in lines))
        self.assertFalse(any("desatualiz" in line.lower() for line in lines))
        self.assertFalse(any(line.startswith("ECMWF:") for line in lines))
        self.assertFalse(any(line.startswith("GFS:") for line in lines))
        self.assertEqual(ctx["tooltip"], "\n".join(lines))


class NextPipelineRunTests(SimpleTestCase):
    def test_iso_matches_local_str_cron(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        fixed_utc = datetime(2026, 5, 20, 11, 30, tzinfo=ZoneInfo("UTC"))
        with patch("lib_efc._next_pipeline_run_local_datetime") as mock_dt:
            mock_dt.return_value = datetime(
                2026, 5, 21, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo")
            )
            self.assertEqual(next_pipeline_run_local_str("0 12 * * *"), "21-05-2026 09:00")
            self.assertTrue(next_pipeline_run_iso("0 12 * * *").startswith("2026-05-21T09:00:00"))


class ConsultarFormatTests(SimpleTestCase):
    def test_markdown_to_safe_html_sections_and_bold(self):
        from monitor_web.services.consultar_format import markdown_to_safe_html

        md = "## Resposta direta\nChuva entre **12** e **45** mm.\n\n- km 379\n- MERGE"
        html_out = markdown_to_safe_html(md)
        self.assertIn("<h3", html_out)
        self.assertIn("<strong>12</strong>", html_out)
        self.assertIn("<li>km 379</li>", html_out)

    def test_format_consultar_table_caption_period(self):
        from monitor_web.services.consultar_format import format_consultar_table_html

        df = pd.DataFrame(
            {
                "date": pd.date_range("2026-05-01", periods=3, freq="D"),
                "km_efc": [379, 379, 380],
                "value": [10.0, 55.0, 3.0],
                "variable": ["precip_24h_mm"] * 3,
                "source": ["merge"] * 3,
            }
        )
        html_out = format_consultar_table_html(df, "Chuva no km 379")
        self.assertIn("efc-table", html_out)
        self.assertIn("Pergunta:", html_out)
        self.assertIn("período", html_out.lower())


class LlmBqContextTests(SimpleTestCase):
    def test_build_result_context_stats_and_sample(self):
        from gcp.llm_bq import build_result_context

        df = pd.DataFrame(
            {
                "date": pd.date_range("2026-05-10", periods=5, freq="D"),
                "km_efc": [379] * 5,
                "variable": ["precip_24h_mm", "precip_24h_mm", "precip_120h_mm", "precip_24h_mm", "precip_24h_mm"],
                "value": [12.0, 52.0, 200.0, 8.0, 60.0],
                "source": ["merge", "ecmwf", "merge", "merge", "ecmwf"],
            }
        )
        ctx = build_result_context(df, bq_catalog_range=("2010-01-01", "2026-05-19"))
        self.assertIn("Total de linhas", ctx)
        self.assertIn("Período nos dados", ctx)
        self.assertIn("Faixa de km: 379", ctx)
        self.assertIn("precip_24h_mm > 49 mm", ctx)
        self.assertIn("Catálogo BigQuery", ctx)

    def test_sql_system_prompt_uses_prefix(self):
        from gcp.llm_bq import _sql_system_prompt

        prompt = _sql_system_prompt("myproj.myds")
        self.assertIn("myproj.myds", prompt)
        self.assertNotIn("climaticsystem.efc_clima.locations", prompt)


class BndmetStationStatusTests(SimpleTestCase):
    def test_last_obs_from_any_fenomeno_not_only_i175(self):
        from lib_efc import _bndmet_accum_last_obs_from_horario_df

        now = pd.Timestamp.now(tz="UTC").floor("h")
        df = pd.DataFrame(
            {
                "dataHora": [now.strftime("%Y-%m-%d %H:%M:%S")],
                "valor": [23.0],
                "codEstacao": ["82565"],
                "codFenomeno": ["I101"],
            }
        )
        start = now - pd.Timedelta(hours=120)
        end = now + pd.Timedelta(hours=1)
        stale_cutoff = now - pd.Timedelta(days=3)
        acc, stale, lo = _bndmet_accum_last_obs_from_horario_df(
            df, start_ts=start, end_ts=end, stale_cutoff=stale_cutoff
        )
        self.assertIn("82565", lo)
        self.assertNotIn("82565", stale)
        self.assertEqual(acc.get("82565", 0.0), 0.0)

    def test_bq_empty_last_obs_falls_back_to_csv(self):
        from lib_efc import get_bndmet_accum_hours_stale

        now = pd.Timestamp.now(tz="UTC").floor("h")
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "bndmet.csv"
            pd.DataFrame(
                {
                    "dataHora": [now.strftime("%Y-%m-%d %H:%M:%S")],
                    "valor": [1.0],
                    "codEstacao": ["A238"],
                    "codFenomeno": ["I175"],
                }
            ).to_csv(csv_path, index=False)
            with patch("lib_efc._bndmet_horario_bq_available", return_value=True):
                with patch("gcp.bq_load_clima._bq_client") as mock_client:
                    mock_client.return_value.query.return_value.to_dataframe.return_value = (
                        pd.DataFrame()
                    )
                    acc, stale, lo = get_bndmet_accum_hours_stale(
                        now.strftime("%Y-%m-%d"),
                        bq_dataset="efc_clima",
                        csv_path=csv_path,
                    )
        self.assertIn("A238", lo)

    def test_expand_last_obs_same_position(self):
        from lib_efc import _bndmet_expand_last_obs_for_catalog

        est = pd.DataFrame(
            {
                "codEstacao": ["82376", "A255"],
                "nome": ["ZE DOCA", "ZE DOCA"],
                "latitude": [-3.26916666, -3.26916666],
                "longitude": [-45.65111111, -45.65111111],
            }
        )
        lo = {"A255": "2026-05-19 18:00 UTC"}
        lo2, st2 = _bndmet_expand_last_obs_for_catalog(est, lo, set())
        self.assertEqual(lo2.get("82376"), lo["A255"])
        self.assertNotIn("82376", st2)

    def test_bq_partial_last_obs_merges_csv(self):
        from lib_efc import get_bndmet_accum_hours_stale

        now = pd.Timestamp.now(tz="UTC").floor("h")
        old = (now - pd.Timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "bndmet.csv"
            pd.DataFrame(
                {
                    "dataHora": [now.strftime("%Y-%m-%d %H:%M:%S")],
                    "valor": [2.0],
                    "codEstacao": ["82376"],
                    "codFenomeno": ["I175"],
                }
            ).to_csv(csv_path, index=False)
            bq_last = pd.DataFrame({"cod": ["A255"], "last_ts": [old]})
            bq_acc = pd.DataFrame({"cod": ["A255"], "mm": [5.0]})
            with patch("lib_efc._bndmet_horario_bq_available", return_value=True):
                with patch("gcp.bq_load_clima._bq_client") as mock_client:
                    mock_client.return_value.query.return_value.to_dataframe.side_effect = [
                        bq_acc,
                        bq_last,
                    ]
                    acc, stale, lo = get_bndmet_accum_hours_stale(
                        now.strftime("%Y-%m-%d"),
                        bq_dataset="efc_clima",
                        csv_path=csv_path,
                    )
        self.assertIn("A255", lo)
        self.assertIn("82376", lo)
        self.assertNotIn("82376", stale)


class MonitorAuthMiddlewareTests(SimpleTestCase):
    def _run_middleware(self, path, *, htmx=False, authenticated=False):
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        from monitor_web.middleware import MonitorAuthMiddleware

        def view(_request):
            from django.http import HttpResponse

            return HttpResponse("ok")

        factory = RequestFactory()
        request = factory.get(path, HTTP_HX_REQUEST="true" if htmx else None)
        SessionMiddleware(lambda r: r).process_request(request)
        request.session["efc_authenticated"] = bool(authenticated)
        request.htmx = bool(htmx)
        return MonitorAuthMiddleware(view)(request)

    @patch("monitor_web.middleware.settings")
    def test_htmx_unauthenticated_returns_401_inline_not_hx_redirect(self, mock_settings):
        mock_settings.MONITOR_AUTH_DISABLED = False
        response = self._run_middleware("/fragments/home/status/", htmx=True)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("HX-Redirect", response)
        self.assertIn(b"Sess", response.content)

    @patch("monitor_web.middleware.settings")
    def test_api_unauthenticated_returns_json_401(self, mock_settings):
        mock_settings.MONITOR_AUTH_DISABLED = False
        response = self._run_middleware("/api/home/alertas-acumulados/")
        self.assertEqual(response.status_code, 401)
        import json

        body = json.loads(response.content.decode())
        self.assertEqual(body["error"], "authentication_required")
        self.assertIn("/login/", body["login_url"])

    @patch("monitor_web.middleware.settings")
    def test_map_embed_unauthenticated_returns_401_inline(self, mock_settings):
        mock_settings.MONITOR_AUTH_DISABLED = False
        response = self._run_middleware("/fragments/home/map-embed/")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(b"window.top.location.replace", response.content)
        self.assertIn(b"entrar", response.content.lower())

    @patch("monitor_web.middleware.settings")
    def test_authenticated_passes_through(self, mock_settings):
        mock_settings.MONITOR_AUTH_DISABLED = False
        response = self._run_middleware("/fragments/home/status/", authenticated=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    @patch("monitor_web.middleware.settings")
    def test_portal_login_submit_is_public(self, mock_settings):
        mock_settings.MONITOR_AUTH_DISABLED = False
        response = self._run_middleware("/portal/login/submit/", htmx=False, authenticated=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")


class RaijinServiceTests(SimpleTestCase):
    PREFIX = "climaticsystem.efc_clima"

    def test_parse_intent_km_series(self):
        from monitor_web.services.raijin import parse_intent

        intent = parse_intent("Chuva 24h no km 379 nos últimos 7 dias")
        self.assertEqual(intent["kind"], "km_series")
        self.assertEqual(intent["km"], 379)
        self.assertEqual(intent["days"], 7)

    def test_template_sql_km_series(self):
        from monitor_web.services.raijin import parse_intent, template_sql

        intent = parse_intent("Precipitação km 400 últimos 14 dias")
        sql = template_sql(intent, table_prefix=self.PREFIX)
        self.assertIn("railway_km_400", sql)
        self.assertIn("precip_24h_mm", sql)
        self.assertIn(self.PREFIX, sql)

    def test_validate_bq_sql_rejects_mutations(self):
        from gcp.llm_bq import validate_bq_sql

        ok, err = validate_bq_sql(
            "DELETE FROM `climaticsystem.efc_clima.clima_series`",
            table_prefix=self.PREFIX,
        )
        self.assertFalse(ok)
        self.assertIsNotNone(err)

    def test_ensure_sql_limit(self):
        from gcp.llm_bq import ensure_sql_limit

        sql = ensure_sql_limit("SELECT 1")
        self.assertIn("LIMIT", sql.upper())

    def test_expand_followup_km(self):
        from monitor_web.services.raijin import expand_followup

        expanded = expand_followup("e no km 400?", {"last_km": 379, "last_days": 14})
        self.assertIn("400", expanded)
        self.assertIn("14", expanded)

    def test_summarize_small_result_no_llm(self):
        from monitor_web.services.raijin import summarize_small_result

        df = pd.DataFrame(
            {
                "date": ["2025-01-01", "2025-01-02"],
                "value": [10.0, 55.0],
                "variable": ["precip_24h_mm", "precip_24h_mm"],
            }
        )
        text = summarize_small_result("teste", df)
        self.assertIn("49", text)
        self.assertIn("Resposta direta", text)

    def test_parse_intent_km_compare_merge_ecmwf(self):
        from monitor_web.services.raijin import parse_intent

        intent = parse_intent("Chuva 24 h no km 379 — MERGE vs ECMWF, últimos 14 dias")
        self.assertEqual(intent["kind"], "km_compare")
        self.assertEqual(intent["km"], 379)
        self.assertEqual(intent["days"], 14)
        self.assertEqual(set(intent["sources"]), {"merge", "ecmwf"})
        self.assertEqual(intent["variables"], ["precip_24h_mm"])

    def test_template_km_compare_only_requested_sources(self):
        from monitor_web.services.raijin import parse_intent, template_sql

        intent = parse_intent("Chuva 24 h no km 379 — MERGE vs ECMWF, últimos 14 dias")
        sql = template_sql(intent, table_prefix=self.PREFIX)
        self.assertIn("'merge'", sql)
        self.assertIn("'ecmwf'", sql)
        self.assertNotIn("'gfs'", sql)
        self.assertIn("AS km", sql)
        self.assertIn("railway_km_379", sql)

    def test_parse_intent_daily_peak_yesterday(self):
        from monitor_web.services.raijin import parse_intent

        intent = parse_intent("Qual foi maior chuva registrada ontem?")
        self.assertEqual(intent["kind"], "daily_peak_railway")
        self.assertEqual(intent["date_ref"], "yesterday")
        self.assertEqual(intent["variables"], ["precip_24h_mm"])

    def test_template_daily_peak_yesterday(self):
        from monitor_web.services.raijin import parse_intent, template_sql

        intent = parse_intent("Qual foi maior chuva registrada ontem?")
        sql = template_sql(intent, table_prefix=self.PREFIX)
        self.assertIn("INTERVAL 1 DAY", sql)
        self.assertIn("precip_24h_mm", sql)
        self.assertIn("ORDER BY s.value DESC", sql)
        self.assertIn("AS km", sql)

    def test_summarize_daily_peak_railway(self):
        from monitor_web.services.raijin import parse_intent, summarize_daily_peak_railway

        intent = parse_intent("Qual foi maior chuva registrada ontem?")
        df = pd.DataFrame(
            {
                "date": ["2026-05-25"],
                "km": [412],
                "value": [38.5],
                "source": ["merge"],
                "variable": ["precip_24h_mm"],
            }
        )
        text = summarize_daily_peak_railway("pergunta", df, intent)
        self.assertIn("38.5", text)
        self.assertIn("km 412", text)
        self.assertIn("ontem", text.lower())

    def test_summarize_km_compare_mentions_km_and_exceedance(self):
        from monitor_web.services.raijin import parse_intent, summarize_km_compare

        intent = parse_intent("Chuva 24 h no km 379 — MERGE vs ECMWF, últimos 14 dias")
        df = pd.DataFrame(
            {
                "date": ["2026-05-20", "2026-05-21", "2026-05-20", "2026-05-21"],
                "km": [379, 379, 379, 379],
                "source": ["merge", "merge", "ecmwf", "ecmwf"],
                "variable": ["precip_24h_mm"] * 4,
                "value": [61.9, 30.0, 40.0, 35.0],
            }
        )
        text = summarize_km_compare("pergunta", df, intent)
        self.assertIn("km 379", text)
        self.assertIn("MERGE", text)
        self.assertIn("ECMWF", text)
        self.assertIn("61.9", text)
        self.assertIn("49", text)
        self.assertNotIn("GFS", text)


class AuthServiceTests(SimpleTestCase):
    def test_portal_min_level_matrix(self):
        from monitor_web.permissions import PORTAL_VIEW_MIN_LEVEL, portal_min_level_for_url_name

        self.assertEqual(portal_min_level_for_url_name("home"), 1)
        self.assertEqual(portal_min_level_for_url_name("home_fragment_alerts"), 1)
        self.assertEqual(portal_min_level_for_url_name("home_fragment_exceed"), 1)
        self.assertEqual(portal_min_level_for_url_name("consultar"), 2)
        self.assertEqual(PORTAL_VIEW_MIN_LEVEL["boletins"], 1)


class PortalLevel3PermissionTests(TestCase):
    def test_blocks_previsao_allows_consultar(self):
        from monitor_web.auth_service import create_portal_user
        from monitor_web.permissions import user_can_access_portal_view

        user, _ = create_portal_user(
            username="_portal_level3_test",
            password="Teste1234!",
            access_level=3,
            valid_days=30,
        )
        self.assertFalse(user_can_access_portal_view(user, "previsao"))
        self.assertFalse(user_can_access_portal_view(user, "previsao_heatmap_api"))
        self.assertTrue(user_can_access_portal_view(user, "consultar"))
        self.assertTrue(user_can_access_portal_view(user, "home"))
        user.delete()


class Era5TableBuildersTests(SimpleTestCase):
    def test_band_and_multi_year_tables(self):
        from gcp.era5_railway_climatology import (
            CATEGORY_ABOVE,
            table_month_by_km_band,
            table_multi_year_pivot_z,
        )

        rows = []
        for km in (0, 25, 50):
            for month in (1, 2):
                rows.append(
                    {
                        "year": 2026,
                        "month": month,
                        "km_efc": km,
                        "precip_month_mm": 120.0 + km,
                        "clim_mean": 100.0,
                        "clim_std": 10.0,
                        "z": 2.0,
                        "anomaly_mm": 20.0,
                        "category": CATEGORY_ABOVE,
                    }
                )
        classified = pd.DataFrame(rows)
        bands = table_month_by_km_band(classified, 2026, 1, band_size=50)
        self.assertGreaterEqual(len(bands), 1)
        self.assertIn("situacao", bands.columns)

        rows[0]["year"] = 2025
        classified2 = pd.DataFrame(rows)
        pivot = table_multi_year_pivot_z(classified2, 0, (2025, 2026))
        self.assertFalse(pivot.empty)


class BoletimEra5StaffTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from monitor_web.auth_service import create_portal_user

        self.staff = User.objects.create_user(
            username="_era5_staff",
            password="Teste1234!",
            is_staff=True,
        )
        self.portal_user, _ = create_portal_user(
            username="_era5_portal_user",
            password="Teste1234!",
            access_level=1,
        )

    @patch("monitor_web.services.boletim_era5_climatology._load_bundle")
    def test_staff_can_open_era5_climatology_page(self, mock_bundle):
        mock_bundle.return_value = None
        self.client.force_login(self.staff)
        response = self.client.get("/boletins/climatologia-era5/?year=2026")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Climatologia ERA5")

    @patch("monitor_web.services.boletim_era5_climatology._load_bundle")
    def test_non_staff_redirected_from_era5(self, mock_bundle):
        mock_bundle.return_value = None
        self.client.force_login(self.portal_user)
        response = self.client.get("/boletins/climatologia-era5/?year=2026")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/") or "home" in response.url)

    def test_portal_has_no_era5_climatology_route(self):
        from monitor_web import portal_urls

        names = [p.name for p in portal_urls.urlpatterns]
        self.assertNotIn("boletim_era5_climatology", names)
        self.assertNotIn("boletim_era5_export", names)

    def test_boletins_ev_options_include_km_408(self):
        import pandas as pd

        from lib_merge_ev import ev_bounds_for_km
        from monitor_web.services.boletins import _ev_bounds, _ev_options

        ev, start, end = _ev_bounds(408)
        self.assertEqual((ev, start, end), (46, 404, 415))
        self.assertNotEqual((ev, start, end), (41, 400, 409))
        _n, _s, _e, kms = ev_bounds_for_km(408)
        self.assertIn(408, kms)
        self.assertEqual(kms, tuple(range(404, 416)))
        df = pd.DataFrame(
            {
                "km_efc": list(range(0, 500, 10)),
                "exceed_days_24": [1] * 50,
                "max_24h_mm": [50] * 50,
            }
        )
        opts = _ev_options(df)
        self.assertGreaterEqual(len(opts), 40)
        self.assertLess(len(opts), 50)
        df_zero = pd.DataFrame({"km_efc": [10], "exceed_days_24": [1], "max_24h_mm": [60]})
        opts_km = _ev_options(df_zero, extra_km=408)
        self.assertTrue(any(o["ev"] == 46 and o["km_start"] == 404 and 408 in o["kms"] for o in opts_km))

    def test_boletins_context_hides_era5_link(self):
        from django.test import RequestFactory

        from monitor_web.services.boletins import build_boletins_context

        rf = RequestFactory()
        req = rf.get("/boletins/?year=2026")
        req.user = self.portal_user
        with patch("monitor_web.services.boletins.bq_read_enabled_from_env", return_value=False):
            ctx = build_boletins_context(req)
        self.assertFalse(ctx.get("show_era5_climatology_link"))

        req.user = self.staff
        with patch("monitor_web.services.boletins.bq_read_enabled_from_env", return_value=False):
            ctx = build_boletins_context(req)
        self.assertFalse(ctx.get("show_era5_climatology_link"))

class PortalAuthTests(TestCase):
    def test_expired_portal_account_blocked(self):
        from datetime import timedelta

        from django.utils import timezone

        from monitor_web.auth_service import authenticate_monitor, create_portal_user
        from monitor_web.models import Audience

        user, profile = create_portal_user(
            username="_expired_portal_user",
            password="Teste1234!",
            access_level=1,
            valid_days=40,
        )
        profile.valid_until = timezone.now() - timedelta(hours=1)
        profile.save(update_fields=["valid_until"])

        result = authenticate_monitor("_expired_portal_user", "Teste1234!", expected_audience=Audience.PORTAL)
        self.assertFalse(result.ok)
        self.assertIn("expirado", (result.error or "").lower())

    def test_portal_user_cannot_use_internal_login(self):
        from monitor_web.auth_service import authenticate_monitor, create_portal_user
        from monitor_web.models import Audience

        user, _ = create_portal_user(
            username="_portal_only_user",
            password="Teste1234!",
            access_level=1,
        )
        result = authenticate_monitor("_portal_only_user", "Teste1234!", expected_audience=Audience.INTERNAL)
        self.assertFalse(result.ok)

    def test_elevated_internal_demo_can_use_internal_and_portal_login(self):
        from monitor_web.auth_service import (
            authenticate_monitor,
            create_portal_user,
            is_internal_operator,
            upsert_internal_staff_user,
        )
        from monitor_web.models import Audience

        create_portal_user(username="_colliding_demo", password="demo", access_level=1)
        user, profile = upsert_internal_staff_user(
            username="_colliding_demo",
            password="demo",
            access_level=2,
            valid_days=3650,
        )
        self.assertTrue(user.is_staff)
        self.assertEqual(profile.audience, Audience.INTERNAL)
        self.assertTrue(is_internal_operator(user))

        internal = authenticate_monitor("_colliding_demo", "demo", expected_audience=Audience.INTERNAL)
        self.assertTrue(internal.ok)
        portal = authenticate_monitor("_colliding_demo", "demo", expected_audience=Audience.PORTAL)
        self.assertTrue(portal.ok)
