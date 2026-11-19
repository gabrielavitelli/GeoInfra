"""Gráficos Plotly → JSON para templates."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from components_efc import style_plotly_figure, style_plotly_subplot_axes


def sanitize_for_json(obj):
    """Convert numpy/pandas scalars and nested containers to JSON-native types."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, np.ndarray):
        return sanitize_for_json(obj.tolist())
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        if np.isnan(v) or np.isinf(v):
            return None
        return v
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, np.datetime64):
        if pd.isna(obj):
            return None
        return pd.Timestamp(obj).isoformat()
    if obj is pd.NA or obj is pd.NaT:
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def fig_to_json(fig) -> str:
    import plotly.io as pio

    try:
        return pio.to_json(fig, validate=False)
    except (TypeError, ValueError):
        return json.dumps(sanitize_for_json(fig.to_plotly_json()))


def llm_chart_from_df(df: pd.DataFrame) -> str | None:
    """Porta _make_chart_from_bq_result do Streamlit."""
    if df is None or df.empty or len(df) < 2:
        return None
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None

    date_col = next((c for c in ["date", "datetime", "time", "data"] if c in df.columns), None)
    if date_col is None:
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                date_col = c
                break
    colors = ["#2563eb", "#dc2626", "#16a34a", "#ea580c", "#7c3aed", "#0891b2"]
    fig = go.Figure()

    if date_col is None:
        km_col = next((c for c in df.columns if str(c).lower() in ("km_efc", "km")), None)
        val_col = next(
            (c for c in df.columns if c != km_col and pd.api.types.is_numeric_dtype(df[c])),
            None,
        )
        if km_col and val_col and len(df) >= 2:
            sub = df[[km_col, val_col]].dropna().sort_values(km_col)
            fig.add_trace(
                go.Bar(
                    x=sub[km_col].astype(float),
                    y=sub[val_col].astype(float),
                    name=str(val_col).replace("_", " "),
                    marker_color=colors[0],
                )
            )
            style_plotly_figure(fig, height=360, showlegend=False)
            return fig_to_json(fig)
        return None

    var_col = next((c for c in ["variable", "variable_name", "var"] if c in df.columns), None)
    val_col = next((c for c in ["value", "valor", "val"] if c in df.columns and pd.api.types.is_numeric_dtype(df[c])), None)
    source_col = "source" if "source" in df.columns else None

    if var_col and val_col:
        df_plot = df[[date_col, var_col, val_col] + ([source_col] if source_col else [])].copy()
        df_plot[date_col] = pd.to_datetime(df_plot[date_col], errors="coerce")
        df_plot = df_plot.dropna(subset=[date_col, val_col])
        if len(df_plot) < 2:
            return None
        if source_col:
            keys = df_plot.groupby([source_col, var_col]).size().reset_index()[[source_col, var_col]].values.tolist()
        else:
            keys = [(None, v) for v in df_plot[var_col].astype(str).unique()]
        for i, (src, var) in enumerate(keys):
            if source_col:
                sub = df_plot[(df_plot[source_col].astype(str) == str(src)) & (df_plot[var_col].astype(str) == str(var))].sort_values(date_col)
                label = f"{str(src).upper()} ({var})"
            else:
                sub = df_plot[df_plot[var_col].astype(str) == str(var)].sort_values(date_col)
                label = str(var)
            if len(sub) < 2:
                continue
            fig.add_trace(
                go.Scatter(
                    x=sub[date_col],
                    y=sub[val_col].astype(float),
                    mode="lines+markers",
                    name=label,
                    line=dict(color=colors[i % len(colors)], width=2),
                )
            )
    else:
        numeric_cols = [c for c in df.columns if c != date_col and pd.api.types.is_numeric_dtype(df[c])]
        if not numeric_cols:
            return None
        df_plot = df[[date_col] + numeric_cols].copy()
        df_plot[date_col] = pd.to_datetime(df_plot[date_col], errors="coerce").dropna()
        for i, col in enumerate(numeric_cols):
            y = df_plot[col].astype(float)
            if y.notna().sum() < 2:
                continue
            fig.add_trace(
                go.Scatter(x=df_plot[date_col], y=y, mode="lines+markers", name=col, line=dict(color=colors[i % len(colors)], width=2))
            )

    if len(fig.data) == 0:
        return None
    style_plotly_figure(fig, height=400, showlegend=True, legend_bottom=True)
    _add_threshold_lines(fig)
    return fig_to_json(fig)


def _add_threshold_lines(fig) -> None:
    import plotly.graph_objects as go

    names = [getattr(t, "name", "") or "" for t in fig.data]
    blob = " ".join(str(n).lower() for n in names)
    if "24" in blob or "precip_24" in blob:
        fig.add_hline(y=49, line_dash="dash", line_color="#fbbf24", annotation_text="49 mm")
    if "120" in blob or "72" in blob:
        fig.add_hline(y=250, line_dash="dash", line_color="#f87171", annotation_text="250 mm")
