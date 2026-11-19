#!/usr/bin/env python3
"""
Avaliação matemática do modelo implantado (cascade_v2) no último verão
com features GOES + rótulos EWS/UNIPLU — sem deploy.

- Features: gs://…/cascade_v2/features_cascade_v2.csv (2026-01-01 → 2026-05-20)
- Rótulo EWS: ews_mm ≥ 49 (já no CSV)
- Rótulo UNIPLU: precip_mm ≥ 49 nas estações do catálogo EFC (r≤20 km),
  mapeadas ao km_efc; UNIPLU no BQ vai até 2025-12-31 (overlap parcial)
- Subsample ~3/4 do espaço amostral (mantém todos os positivos)
- Filtro VIF (tol=0.2 ⇒ VIF≤5) antes de treinar o SVM
- Expansão de volume: densificar km (--dense-km-step) + sintéticos eigen
  (--train-mult 10 ⇒ ~10× linhas de treino vs base)
- Figuras: ROC/AUC, AG (fitness), SVM (scores), VIF, eigen-synth

Uso:
  python3 scripts/raijin_health/eval_summer_roc.py \\
    --out-dir /opt/cursor/artifacts/raijin_eval
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from google.cloud import bigquery, storage
from sklearn.metrics import (
    auc,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

PROJECT = os.environ.get("GCP_PROJECT", "climaticsystem")
BUCKET = os.environ.get("GCS_BUCKET", "efc-clima-data")
FEATURES_BLOB = "ml/raijin-trainkit/data/ml/cascade_v2/features_cascade_v2.csv"
MODEL_BLOB = "ml/raijin-trainkit/data/ml/cascade_v2/cascade_v2_model.joblib"
THRESHOLD_MM = 49.0
# Verão / chuvoso operacional EFC (abrangente)
SUMMER_MONTHS = {10, 11, 12, 1, 2, 3, 4, 5}


def _download(blob_name: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    storage.Client(project=PROJECT).bucket(BUCKET).blob(blob_name).download_to_filename(
        str(dest)
    )
    return dest


def _load_uniplu_near_kms(kms: list[int], d0: str, d1: str, catalog: Path) -> pd.DataFrame:
    """day × km_efc × uniplu_mm — estação do catálogo mais próxima de cada km EWS."""
    if not catalog.is_file():
        return pd.DataFrame(columns=["day", "km_efc", "uniplu_mm"])
    cat = pd.read_csv(catalog)
    cat["km_efc"] = pd.to_numeric(cat["km_efc"], errors="coerce")
    cat = cat.dropna(subset=["km_efc"]).copy()
    cat["km_efc"] = cat["km_efc"].round().astype(int)
    # mapa km EWS → km catálogo mais próximo (≤ 25 km)
    cat_kms = np.array(sorted(cat["km_efc"].unique()))
    rows = []
    for k in kms:
        j = int(np.argmin(np.abs(cat_kms - int(k))))
        if abs(int(cat_kms[j]) - int(k)) > 25:
            continue
        rows.append({"km_efc": int(k), "km_cat": int(cat_kms[j])})
    if not rows:
        return pd.DataFrame(columns=["day", "km_efc", "uniplu_mm"])
    bridge = pd.DataFrame(rows)
    cat_st = cat.rename(columns={"km_efc": "km_cat"})
    cat2 = bridge.merge(cat_st, on="km_cat", how="inner")  # km_efc = EWS km
    loc_ids = [f"uniplu_{c}" for c in cat2["cod"].astype(str).unique()]
    client = bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT s.date AS day, s.location_id, s.value AS uniplu_mm
    FROM `{PROJECT}.efc_clima.clima_series` s
    WHERE s.source = 'uniplu' AND s.variable = 'precip_mm'
      AND s.date BETWEEN @d0 AND @d1
      AND s.location_id IN UNNEST(@locs)
    """
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("d0", "DATE", d0),
                bigquery.ScalarQueryParameter("d1", "DATE", d1),
                bigquery.ArrayQueryParameter("locs", "STRING", loc_ids),
            ]
        ),
    )
    raw = job.to_dataframe(create_bqstorage_client=False)
    if raw.empty:
        return pd.DataFrame(columns=["day", "km_efc", "uniplu_mm"])
    raw["day"] = pd.to_datetime(raw["day"]).dt.normalize()
    raw["cod"] = raw["location_id"].str.replace("^uniplu_", "", regex=True)
    m = raw.merge(cat2[["cod", "km_efc"]].drop_duplicates(), on="cod", how="inner")
    return m.groupby(["day", "km_efc"], as_index=False)["uniplu_mm"].max()


def _subsample(df: pd.DataFrame, keep_frac: float, seed: int) -> pd.DataFrame:
    """Mantém todos os positivos; amostra keep_frac dos negativos (~3/4 do espaço)."""
    rng = np.random.default_rng(seed)
    pos = df[df["label_combo"] == 1]
    neg = df[df["label_combo"] == 0]
    n_neg = max(1, int(round(len(neg) * keep_frac)))
    if len(neg) > n_neg:
        neg = neg.sample(n=n_neg, random_state=seed)
    out = pd.concat([pos, neg], ignore_index=True)
    out = out.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    return out


def _interp_feat_cols(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if c.startswith("C") and "_h" in c:
            cols.append(c)
        elif c.startswith("bq_ecmwf_"):
            cols.append(c)
        elif c in (
            "wn2_precip_mm",
            "wn2_t2m_c",
            "wn2_conv_proxy",
            "merge_mm_24h",
            "goes_hours_found",
            "lat",
            "lon",
        ):
            cols.append(c)
    return cols


def _densify_km_grid(df: pd.DataFrame, step: int) -> pd.DataFrame:
    """Expande malha de km por interpolação linear das features GOES/ECMWF.

    Rótulos (ews_mm / uniplu_mm) vêm do km âncora mais próximo (não se interpola
    o evento). Linhas novas marcam ``is_densified=1``.
    """
    if step is None or step <= 0:
        out = df.copy()
        out["is_densified"] = 0
        return out
    work = df.copy()
    work["km_efc"] = pd.to_numeric(work["km_efc"], errors="coerce")
    work = work.dropna(subset=["km_efc"]).copy()
    work["km_efc"] = work["km_efc"].astype(int)
    anchors = np.array(sorted(work["km_efc"].unique()))
    if len(anchors) < 2:
        work["is_densified"] = 0
        return work
    grid = np.unique(
        np.concatenate(
            [anchors, np.arange(int(anchors.min()), int(anchors.max()) + 1, int(step))]
        )
    )
    feat_cols = _interp_feat_cols(work)
    rows: list[dict] = []
    for day, g in work.groupby("day", sort=True):
        g = g.sort_values("km_efc")
        x = g["km_efc"].to_numpy(dtype=float)
        # dedup km no mesmo dia
        _, uniq_idx = np.unique(x, return_index=True)
        g = g.iloc[uniq_idx]
        x = g["km_efc"].to_numpy(dtype=float)
        if len(x) < 2:
            for _, r in g.iterrows():
                d = r.to_dict()
                d["is_densified"] = 0
                rows.append(d)
            continue
        interp = {}
        for c in feat_cols:
            yv = pd.to_numeric(g[c], errors="coerce").to_numpy(dtype=float)
            # preenche NaN locais com vizinho antes de interpolar
            if np.all(np.isnan(yv)):
                continue
            mask = ~np.isnan(yv)
            if mask.sum() == 0:
                continue
            interp[c] = np.interp(grid.astype(float), x[mask], yv[mask])
        ews = pd.to_numeric(g.get("ews_mm"), errors="coerce").fillna(0).to_numpy(dtype=float)
        uni = (
            pd.to_numeric(g["uniplu_mm"], errors="coerce").fillna(0).to_numpy(dtype=float)
            if "uniplu_mm" in g.columns
            else np.zeros(len(g))
        )
        for i, km in enumerate(grid):
            j = int(np.argmin(np.abs(x - km)))
            is_anchor = bool(np.any(np.abs(x - km) < 0.5))
            rec = {
                "day": day,
                "km_efc": int(km),
                "ews_mm": float(ews[j]),
                "uniplu_mm": float(uni[j]),
                "is_densified": 0 if is_anchor else 1,
                "is_synth_goes": int(g.iloc[j]["is_synth_goes"])
                if "is_synth_goes" in g.columns
                else 0,
            }
            for c, arr in interp.items():
                rec[c] = float(arr[i])
            # copia metadados do âncora
            for meta in ("init_tag", "hard_negative", "sample_weight", "wn2_has_data", "label"):
                if meta in g.columns:
                    rec[meta] = g.iloc[j][meta]
            rows.append(rec)
    out = pd.DataFrame(rows)
    out["label_ews"] = (out["ews_mm"].fillna(0) >= THRESHOLD_MM).astype(int)
    out["label_uniplu"] = (out["uniplu_mm"].fillna(0) >= THRESHOLD_MM).astype(int)
    out["label_combo"] = ((out["label_ews"] == 1) | (out["label_uniplu"] == 1)).astype(int)
    return out


def _synth_factor_for_mult(n_train: int, n_base: int, target_mult: float, mode: str) -> float:
    """Calcula factor eigen para atingir ~target_mult × n_base no fit."""
    if target_mult <= 1 or n_train <= 0 or n_base <= 0:
        return 0.0
    target = target_mult * n_base
    # com mode=both, factor f ⇒ n_fit ≈ n_train * (1+f)
    # com mode=pos, só positivos crescem — approx n_fit ≈ n_train + f*n_pos ≈ n_train*(1+f/2) se balanceado
    if mode == "both":
        return max(0.0, target / n_train - 1.0)
    # pos/neg: assume ~metade da classe alvo
    return max(0.0, 2.0 * (target / n_train - 1.0))


def _load_merge_km_daily(kms: list[int], d0: str, d1: str) -> pd.DataFrame:
    """day × km_efc × merge_mm (precip_24h) nos railway_km_*."""
    if not kms:
        return pd.DataFrame(columns=["day", "km_efc", "merge_mm"])
    locs = [f"railway_km_{int(k)}" for k in kms]
    client = bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT s.date AS day, s.location_id, s.value AS merge_mm
    FROM `{PROJECT}.efc_clima.clima_series` s
    WHERE s.source = 'merge' AND s.variable = 'precip_24h_mm'
      AND s.date BETWEEN @d0 AND @d1
      AND s.location_id IN UNNEST(@locs)
    """
    raw = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("d0", "DATE", d0),
                bigquery.ScalarQueryParameter("d1", "DATE", d1),
                bigquery.ArrayQueryParameter("locs", "STRING", locs),
            ]
        ),
    ).to_dataframe(create_bqstorage_client=False)
    if raw.empty:
        return pd.DataFrame(columns=["day", "km_efc", "merge_mm"])
    raw["day"] = pd.to_datetime(raw["day"]).dt.normalize()
    raw["km_efc"] = raw["location_id"].str.replace("railway_km_", "", regex=False).astype(int)
    return raw.groupby(["day", "km_efc"], as_index=False)["merge_mm"].max()


def _sample_class_features(
    X_bank: np.ndarray,
    n: int,
    *,
    seed: int,
    eig_floor: float = 1e-6,
) -> np.ndarray:
    """Amostra n vetores ~ N(μ, Σ) via autovalores/autovetores do banco da classe."""
    rng = np.random.default_rng(seed)
    if len(X_bank) == 0 or n <= 0:
        return np.zeros((0, X_bank.shape[1] if X_bank.ndim == 2 else 0))
    if len(X_bank) == 1:
        return np.repeat(X_bank, n, axis=0)
    mu = X_bank.mean(axis=0)
    Xc = X_bank - mu
    cov = (Xc.T @ Xc) / max(len(X_bank) - 1, 1)
    cov = 0.5 * (cov + cov.T)
    evals, evecs = np.linalg.eigh(cov)
    evals = np.clip(evals, eig_floor, None)
    order = np.argsort(evals)[::-1]
    evals, evecs = evals[order], evecs[:, order]
    z = rng.normal(size=(n, len(evals)))
    return mu + (z * np.sqrt(evals)) @ evecs.T


def _extend_calendar_synth_goes(
    df_goes: pd.DataFrame,
    feat_cols: list[str],
    *,
    uni: pd.DataFrame,
    merge: pd.DataFrame,
    seed: int = 42,
) -> pd.DataFrame:
    """Une calendário GOES real + dias extras (UNIPLU/MERGE) com GOES sintético eigen.

    Rótulo do dia extra: UNIPLU∪MERGE ≥49. Features GOES/ECMWF amostradas da
    distribuição empírico-espectral da mesma classe nos dias com GOES real.
    """
    base = df_goes.copy()
    base["day"] = pd.to_datetime(base["day"]).dt.normalize()
    base["km_efc"] = pd.to_numeric(base["km_efc"], errors="coerce").astype(int)
    base["is_synth_goes"] = 0
    if "label_combo" not in base.columns:
        base["label_ews"] = (
            pd.to_numeric(base.get("ews_mm"), errors="coerce").fillna(0) >= THRESHOLD_MM
        ).astype(int)
        base["label_uniplu"] = 0
        base["label_combo"] = base["label_ews"]

    # banco de features por classe (GOES real)
    filled = _fill_numeric(base, feat_cols)
    X_all = filled[feat_cols].values.astype(float)
    y_all = filled["label_combo"].values.astype(int)
    banks = {0: X_all[y_all == 0], 1: X_all[y_all == 1]}
    # fallback se uma classe tiver pouco dado
    for lab in (0, 1):
        if len(banks[lab]) < 3:
            banks[lab] = X_all

    # calendário candidato: uni ∪ merge
    parts = []
    if uni is not None and not uni.empty:
        u = uni.copy()
        u["day"] = pd.to_datetime(u["day"]).dt.normalize()
        parts.append(u.rename(columns={"uniplu_mm": "precip_mm"})[["day", "km_efc", "precip_mm"]])
    if merge is not None and not merge.empty:
        m = merge.copy()
        m["day"] = pd.to_datetime(m["day"]).dt.normalize()
        parts.append(m.rename(columns={"merge_mm": "precip_mm"})[["day", "km_efc", "precip_mm"]])
    if not parts:
        return base

    cal = pd.concat(parts, ignore_index=True)
    cal = cal.groupby(["day", "km_efc"], as_index=False)["precip_mm"].max()
    cal = cal[cal["day"].dt.month.isin(SUMMER_MONTHS)].copy()
    # remove pares já cobertos por GOES real
    key = set(zip(base["day"].astype(str), base["km_efc"].astype(int)))
    cal["k"] = list(zip(cal["day"].astype(str), cal["km_efc"].astype(int)))
    cal = cal[~cal["k"].isin(key)].drop(columns=["k"])
    if cal.empty:
        return base

    cal["label_combo"] = (cal["precip_mm"] >= THRESHOLD_MM).astype(int)
    # amostra features sintéticas
    rows = []
    for lab, g in cal.groupby("label_combo"):
        Xs = _sample_class_features(banks[int(lab)], len(g), seed=seed + int(lab) * 17)
        for i, (_, r) in enumerate(g.iterrows()):
            rec = {
                "day": r["day"],
                "km_efc": int(r["km_efc"]),
                "ews_mm": 0.0,
                "uniplu_mm": float(r["precip_mm"]) if "uniplu_mm" in str(parts) else np.nan,
                "merge_mm_24h": float(r["precip_mm"]),
                "label_ews": 0,
                "label_uniplu": int(lab),
                "label_combo": int(lab),
                "is_synth_goes": 1,
                "is_densified": 0,
            }
            for j, c in enumerate(feat_cols):
                rec[c] = float(Xs[i, j])
            rows.append(rec)
    extra = pd.DataFrame(rows)
    out = pd.concat([base, extra], ignore_index=True, sort=False)
    return out


def _balance_per_class(
    df: pd.DataFrame,
    feat_cols: list[str],
    *,
    per_class: int,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Monta X,y de treino com ~per_class positivos e ~per_class negativos (eigen)."""
    work = _fill_numeric(df, feat_cols)
    X = work[feat_cols].values.astype(float)
    y = work["label_combo"].values.astype(int)
    Xs, ys = [], []
    meta = {}
    rng_seed = seed
    for lab in (0, 1):
        Xl = X[y == lab]
        n_real = len(Xl)
        if n_real == 0:
            raise RuntimeError(f"classe {lab} vazia — não dá para balancear")
        need = max(int(per_class), n_real)
        # mantém todos os reais + completa com eigen até need
        n_syn = max(0, need - n_real)
        if n_syn > 0:
            syn = _sample_class_features(Xl, n_syn, seed=rng_seed + lab)
            Xc = np.vstack([Xl, syn])
        else:
            # subsample se já tem demais
            rng = np.random.default_rng(rng_seed + lab)
            idx = rng.choice(n_real, size=need, replace=False)
            Xc = Xl[idx]
            n_syn = 0
        Xs.append(Xc)
        ys.append(np.full(len(Xc), lab, dtype=int))
        meta[str(lab)] = {"n_real": int(n_real), "n_out": int(len(Xc)), "n_synth": int(n_syn)}
    return np.vstack(Xs), np.concatenate(ys), meta


def _fill_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    work = df.copy()
    for c in cols:
        if c not in work.columns:
            work[c] = np.nan
        work[c] = pd.to_numeric(work[c], errors="coerce")
        med = work[c].median(skipna=True)
        work[c] = work[c].fillna(0.0 if pd.isna(med) else float(med))
    return work


def _gate_precip(work: pd.DataFrame) -> pd.Series:
    precip = pd.to_numeric(work.get("wn2_precip_mm"), errors="coerce")
    if "merge_mm_24h" in work.columns:
        precip = precip.fillna(pd.to_numeric(work["merge_mm_24h"], errors="coerce"))
    if "bq_ecmwf_precip_24h_mm" in work.columns:
        precip = precip.fillna(pd.to_numeric(work["bq_ecmwf_precip_24h_mm"], errors="coerce"))
    return precip.fillna(0.0)


def _vif_values(X: np.ndarray) -> np.ndarray:
    """VIF = 1 / (1 − R²) via regressão linear de cada coluna nas demais."""
    n, p = X.shape
    if p < 2:
        return np.ones(p)
    # padroniza para estabilidade numérica
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    Z = (X - mu) / sd
    vifs = np.ones(p)
    for i in range(p):
        y = Z[:, i]
        A = np.delete(Z, i, axis=1)
        # mínimos quadrados; se singular, VIF alto
        try:
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
            yhat = A @ coef
            ss_res = float(np.sum((y - yhat) ** 2))
            ss_tot = float(np.sum((y - y.mean()) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0
            r2 = min(max(r2, 0.0), 1.0 - 1e-12)
            vifs[i] = 1.0 / (1.0 - r2)
        except np.linalg.LinAlgError:
            vifs[i] = np.inf
    return vifs


def _vif_filter(
    df: pd.DataFrame,
    feat_cols: list[str],
    *,
    tol: float = 0.2,
    min_features: int = 3,
) -> dict:
    """Remove features colineares até tolerância ≥ tol (VIF ≤ 1/tol).

    ``tol=0.2`` ⇒ VIF máximo permitido = 5.0 (critério clássico de multicolinearidade).
    """
    max_vif = 1.0 / float(tol) if tol > 0 else np.inf
    cols = list(feat_cols)
    work = _fill_numeric(df, cols)
    dropped: list[dict] = []
    while len(cols) > min_features:
        X = work[cols].values.astype(float)
        vifs = _vif_values(X)
        i_max = int(np.nanargmax(vifs))
        if float(vifs[i_max]) <= max_vif + 1e-9:
            break
        dropped.append({"feature": cols[i_max], "vif": float(vifs[i_max])})
        cols.pop(i_max)
    final_vifs = _vif_values(work[cols].values.astype(float))
    return {
        "kept": cols,
        "dropped": dropped,
        "tol": float(tol),
        "max_vif": float(max_vif),
        "vif_final": {c: float(v) for c, v in zip(cols, final_vifs)},
    }


def _eigen_synthetic(
    X: np.ndarray,
    y: np.ndarray,
    *,
    factor: float = 1.0,
    mode: str = "both",
    seed: int = 42,
    eig_floor: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Gera amostras sintéticas via decomposição espectral da covariância.

    Para cada classe: Σ = V Λ Vᵀ, amostra z∼N(0,I),
    x' = μ + V · √Λ · z  (Gaussian no espaço das componentes principais).

    ``factor`` = quantas sintéticas por amostra real (1.0 ⇒ dobra a classe).
    ``mode``: ``both`` | ``pos`` | ``neg``.
    """
    if factor <= 0:
        return X, y, {"n_synth": 0, "per_class": {}}
    rng = np.random.default_rng(seed)
    classes = []
    if mode in ("both", "pos"):
        classes.append(1)
    if mode in ("both", "neg"):
        classes.append(0)
    Xs, ys = [X], [y]
    meta_per: dict = {}
    for lab in classes:
        Xl = X[y == lab]
        if len(Xl) < 3:
            meta_per[str(lab)] = {"n_real": int(len(Xl)), "n_synth": 0, "skip": "n<3"}
            continue
        mu = Xl.mean(axis=0)
        Xc = Xl - mu
        # covariância amostral; eigh em Gram simétrica
        cov = (Xc.T @ Xc) / max(len(Xl) - 1, 1)
        cov = 0.5 * (cov + cov.T)
        evals, evecs = np.linalg.eigh(cov)
        evals = np.clip(evals, eig_floor, None)
        # ordem decrescente
        order = np.argsort(evals)[::-1]
        evals, evecs = evals[order], evecs[:, order]
        n_syn = max(1, int(round(len(Xl) * float(factor))))
        z = rng.normal(size=(n_syn, len(evals)))
        # x' = μ + V √Λ zᵀ
        synth = mu + (z * np.sqrt(evals)) @ evecs.T
        Xs.append(synth)
        ys.append(np.full(n_syn, lab, dtype=int))
        meta_per[str(lab)] = {
            "n_real": int(len(Xl)),
            "n_synth": int(n_syn),
            "top_eigenvalues": [float(v) for v in evals[:5]],
            "explained_var_top5": float(evals[:5].sum() / evals.sum()),
        }
    X_out = np.vstack(Xs)
    y_out = np.concatenate(ys)
    return X_out, y_out, {"n_synth": int(len(y_out) - len(y)), "per_class": meta_per}


def _plot_eigen_synth(
    X_real: np.ndarray,
    y_real: np.ndarray,
    X_aug: np.ndarray,
    y_aug: np.ndarray,
    out: Path,
) -> None:
    """Projeção PC1×PC2: reais vs sintéticos."""
    mu = X_real.mean(axis=0)
    Xc = X_real - mu
    cov = (Xc.T @ Xc) / max(len(X_real) - 1, 1)
    cov = 0.5 * (cov + cov.T)
    evals, evecs = np.linalg.eigh(cov)
    order = np.argsort(evals)[::-1]
    W = evecs[:, order[:2]]
    ev = evals[order[:2]]
    n_real = len(X_real)
    Pr = (X_real - mu) @ W
    # sintéticos = linhas depois das reais no conjunto aumentado
    if len(X_aug) > n_real:
        Ps = (X_aug[n_real:] - mu) @ W
        ys = y_aug[n_real:]
    else:
        Ps = np.zeros((0, 2))
        ys = np.array([], dtype=int)

    fig, ax = plt.subplots(figsize=(6.6, 5.4))
    for lab, color, name in [(0, "#64748b", "Neg real"), (1, "#dc2626", "Pos real")]:
        m = y_real == lab
        ax.scatter(Pr[m, 0], Pr[m, 1], s=28, c=color, alpha=0.75, label=f"{name} (n={m.sum()})")
    for lab, color, name in [(0, "#94a3b8", "Neg synth"), (1, "#fca5a5", "Pos synth")]:
        m = ys == lab
        if m.any():
            ax.scatter(
                Ps[m, 0],
                Ps[m, 1],
                s=18,
                c=color,
                alpha=0.45,
                marker="x",
                label=f"{name} (n={m.sum()})",
            )
    ax.set_xlabel(f"PC1 (λ={ev[0]:.2g})")
    ax.set_ylabel(f"PC2 (λ={ev[1]:.2g})")
    ax.set_title("Sintéticos por autovalores/autovetores (espaço PCA)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _fit_svm_cascade(
    df: pd.DataFrame,
    feat_cols: list[str],
    *,
    t_gate: float = 10.0,
    seed: int = 42,
    train_end: str = "2026-03-31",
    synth_factor: float = 0.0,
    synth_mode: str = "both",
    synth_plot: Path | None = None,
    balance_per_class: int = 0,
) -> tuple[pd.DataFrame, dict]:
    """Treina SVM (RBF) nas features pós-VIF (fit só até train_end) + gate precip."""
    work = _fill_numeric(df, feat_cols)
    # treino temporal: se extendermos calendário, train_end pode ser tarde —
    # usa tudo com GOES synth/real até train_end
    train_mask = work["day"] <= pd.Timestamp(train_end)
    if train_mask.sum() < 20 or work.loc[train_mask, "label_combo"].nunique() < 2:
        train_mask = pd.Series(True, index=work.index)
    y_tr = work.loc[train_mask, "label_combo"].values.astype(int)
    X_tr = work.loc[train_mask, feat_cols].values.astype(float)
    scaler = StandardScaler()
    Xs_tr = scaler.fit_transform(X_tr)
    if balance_per_class and balance_per_class > 0:
        Xs_fit, y_fit, balance_meta = _balance_arrays(
            Xs_tr, y_tr, per_class=balance_per_class, seed=seed
        )
        synth_meta = {
            "balance_per_class": balance_meta,
            "n_synth": int(max(0, len(y_fit) - len(y_tr))),
        }
    else:
        Xs_fit, y_fit, synth_meta = _eigen_synthetic(
            Xs_tr, y_tr, factor=synth_factor, mode=synth_mode, seed=seed
        )
    if synth_plot is not None and (synth_factor > 0 or balance_per_class > 0):
        _plot_eigen_synth(Xs_tr, y_tr, Xs_fit, y_fit, synth_plot)
    clf = SVC(
        C=1.0,
        gamma="scale",
        kernel="rbf",
        class_weight="balanced",
        random_state=seed,
    )
    clf.fit(Xs_fit, y_fit)
    X_all = scaler.transform(work[feat_cols].values.astype(float))
    scores = clf.decision_function(X_all)
    # limiar Youden: só linhas com GOES real (não synth_goes), no treino
    real_tr = train_mask.copy()
    if "is_synth_goes" in work.columns:
        real_tr = real_tr & (work["is_synth_goes"] == 0)
    if real_tr.sum() >= 10 and work.loc[real_tr, "label_combo"].nunique() > 1:
        X_you = scaler.transform(work.loc[real_tr, feat_cols].values.astype(float))
        y_you = work.loc[real_tr, "label_combo"].values.astype(int)
        scores_you = clf.decision_function(X_you)
    else:
        y_you, scores_you = y_tr, clf.decision_function(Xs_tr)
    fpr, tpr, thr = roc_curve(y_you, scores_you)
    youden = tpr - fpr
    t_svm = float(thr[int(np.argmax(youden))]) if len(thr) else 0.0
    precip = _gate_precip(work)
    work["svm_score"] = scores
    work["gate_precip_mm"] = precip
    work["pred_cascade"] = ((work["svm_score"] >= t_svm) & (precip >= t_gate)).astype(int)
    work["t_svm"] = t_svm
    work["t_gate"] = t_gate
    pack = {
        "scaler": scaler,
        "model": clf,
        "features": list(feat_cols),
        "svm_threshold": t_svm,
        "wn2_precip_min_mm": t_gate,
        "train_end": train_end,
        "n_train": int(train_mask.sum()),
        "n_train_fit": int(len(y_fit)),
        "n_train_pos_fit": int(np.sum(y_fit == 1)),
        "n_train_neg_fit": int(np.sum(y_fit == 0)),
        "synth": synth_meta,
    }
    return work, pack


def _balance_arrays(
    X: np.ndarray,
    y: np.ndarray,
    *,
    per_class: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """50/50: cada classe com ``per_class`` amostras (reais + eigen)."""
    Xs, ys = [], []
    meta = {}
    for lab in (0, 1):
        Xl = X[y == lab]
        n_real = len(Xl)
        if n_real == 0:
            raise RuntimeError(f"classe {lab} vazia")
        need = max(int(per_class), 1)
        if n_real >= need:
            rng = np.random.default_rng(seed + lab)
            Xl_use = Xl[rng.choice(n_real, size=need, replace=False)]
            n_syn = 0
        else:
            syn = _sample_class_features(Xl, need - n_real, seed=seed + lab)
            Xl_use = np.vstack([Xl, syn])
            n_syn = need - n_real
        Xs.append(Xl_use)
        ys.append(np.full(len(Xl_use), lab, dtype=int))
        meta[str(lab)] = {"n_real": int(n_real), "n_out": int(len(Xl_use)), "n_synth": int(n_syn)}
    return np.vstack(Xs), np.concatenate(ys), meta


def _score_deployed(df: pd.DataFrame, pack: dict) -> pd.DataFrame:
    feat_cols = list(pack["features"])
    work = _fill_numeric(df, feat_cols)
    X = pack["scaler"].transform(work[feat_cols].values.astype(float))
    work["svm_score"] = pack["model"].decision_function(X)
    t_svm = float(pack["svm_threshold"])
    t_gate = float(pack.get("wn2_precip_min_mm", 10.0))
    precip = _gate_precip(work)
    work["gate_precip_mm"] = precip
    work["pred_cascade"] = (
        (work["svm_score"] >= t_svm) & (precip >= t_gate)
    ).astype(int)
    work["t_svm"] = t_svm
    work["t_gate"] = t_gate
    return work


@dataclass
class GAConfig:
    population: int = 12
    n_generations: int = 12
    cv_folds: int = 3
    random_seed: int = 42
    mutation_rate: float = 0.08


def _run_ag_compact(df: pd.DataFrame, feat_cols: list[str], cfg: GAConfig) -> dict:
    """AG enxuto (~3/4 do espaço de features via mask inicial 0.35) para figura de fitness."""
    sub = df.dropna(subset=["label_combo"]).copy()
    for c in feat_cols:
        sub[c] = pd.to_numeric(sub[c], errors="coerce")
        med = sub[c].median(skipna=True)
        sub[c] = sub[c].fillna(0.0 if pd.isna(med) else float(med))
    X = sub[feat_cols].values.astype(float)
    y = sub["label_combo"].values.astype(int)
    if len(np.unique(y)) < 2:
        raise RuntimeError("AG precisa de positivos e negativos")

    rng = random.Random(cfg.random_seed)
    np.random.seed(cfg.random_seed)
    n_feat = X.shape[1]
    cv = StratifiedKFold(
        n_splits=min(cfg.cv_folds, max(2, int(y.sum()))),
        shuffle=True,
        random_state=cfg.random_seed,
    )

    def eval_ind(mask, c_log, g_log) -> float:
        idx = np.where(mask)[0]
        if idx.size < 3:
            return 0.0
        Xi = X[:, idx]
        C = 10.0**c_log
        gamma = 10.0**g_log
        scores = []
        for tr, va in cv.split(Xi, y):
            if len(np.unique(y[tr])) < 2 or len(np.unique(y[va])) < 2:
                continue
            sc = StandardScaler()
            clf = SVC(C=C, gamma=gamma, kernel="rbf", class_weight="balanced")
            clf.fit(sc.fit_transform(Xi[tr]), y[tr])
            try:
                scores.append(roc_auc_score(y[va], clf.decision_function(sc.transform(Xi[va]))))
            except ValueError:
                scores.append(f1_score(y[va], clf.predict(sc.transform(Xi[va])), zero_division=0))
        return float(np.mean(scores)) if scores else 0.0

    def rand_ind():
        mask = np.array([rng.random() < 0.35 for _ in range(n_feat)], dtype=bool)
        if mask.sum() < 3:
            for i in rng.sample(range(n_feat), min(3, n_feat)):
                mask[i] = True
        return mask, rng.uniform(-2, 2), rng.uniform(-3, 0)

    pop = [rand_ind() for _ in range(cfg.population)]
    history = []
    best_fit, best_ind = -1.0, pop[0]
    for gen in range(cfg.n_generations):
        fits = [eval_ind(*ind) for ind in pop]
        gi = int(np.argmax(fits))
        if fits[gi] > best_fit:
            best_fit, best_ind = fits[gi], pop[gi]
        selected = [feat_cols[i] for i, on in enumerate(best_ind[0]) if on]
        history.append(
            {
                "generation": gen + 1,
                "best_fitness": fits[gi],
                "global_best_fitness": best_fit,
                "n_features": len(selected),
            }
        )
        print(f"  AG gen {gen+1}/{cfg.n_generations}: best={fits[gi]:.4f} global={best_fit:.4f}")
        ranked = sorted(zip(fits, pop), key=lambda x: x[0], reverse=True)
        elites = [ind for _, ind in ranked[: max(2, cfg.population // 5)]]
        new_pop = elites.copy()
        while len(new_pop) < cfg.population:
            a, b = rng.choice(elites), rng.choice(elites)
            child_mask = np.array(
                [a[0][i] if rng.random() < 0.5 else b[0][i] for i in range(n_feat)]
            )
            child = (child_mask, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
            # mutate
            mask, c_log, g_log = child
            mask = mask.copy()
            for i in range(n_feat):
                if rng.random() < cfg.mutation_rate:
                    mask[i] = not mask[i]
            if mask.sum() < 3:
                for i in rng.sample(range(n_feat), min(3, n_feat)):
                    mask[i] = True
            if rng.random() < cfg.mutation_rate:
                c_log += rng.uniform(-0.5, 0.5)
            if rng.random() < cfg.mutation_rate:
                g_log += rng.uniform(-0.5, 0.5)
            new_pop.append((mask, c_log, g_log))
        pop = new_pop

    return {
        "best_fitness": best_fit,
        "selected_features": [feat_cols[i] for i, on in enumerate(best_ind[0]) if on],
        "history": history,
        "C": float(10.0 ** best_ind[1]),
        "gamma": float(10.0 ** best_ind[2]),
        "n_generations": cfg.n_generations,
        "population": cfg.population,
    }


def _aggregate_day(scored: pd.DataFrame) -> pd.DataFrame:
    """Mesma agregação do treino cascade_v2 (level=day): max por dia."""
    return (
        scored.groupby("day", as_index=False)
        .agg(
            label_combo=("label_combo", "max"),
            label_ews=("label_ews", "max"),
            label_uniplu=("label_uniplu", "max"),
            svm_score=("svm_score", "max"),
            pred_cascade=("pred_cascade", "max"),
            gate_precip_mm=("gate_precip_mm", "max"),
            ews_mm=("ews_mm", "max"),
            uniplu_mm=("uniplu_mm", "max"),
        )
        .sort_values("day")
        .reset_index(drop=True)
    )


def _roc_block(y, s, pred) -> dict:
    fpr, tpr, _ = roc_curve(y, s)
    roc_auc = float(auc(fpr, tpr))
    cm = confusion_matrix(y, pred, labels=[0, 1])
    tn, fp, fn, tp = (int(x) for x in cm.ravel())
    pod = tp / (tp + fn) if (tp + fn) else 0.0
    far = fp / (tp + fp) if (tp + fp) else 0.0
    fpr_op = fp / (fp + tn) if (fp + tn) else 0.0
    return {
        "fpr": fpr,
        "tpr": tpr,
        "roc_auc": roc_auc,
        "pod": float(pod),
        "far": float(far),
        "precision": float(tp / (tp + fp) if (tp + fp) else 0.0),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "n": int(len(y)),
        "n_pos": int(np.sum(y)),
        "fpr_op": float(fpr_op),
    }


def _merge_hist_goes(df: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    """Une features GOES históricas reais; marca is_synth_goes=0."""
    if hist is None or hist.empty:
        return df
    h = hist.copy()
    h["day"] = pd.to_datetime(h["day"]).dt.normalize()
    h["km_efc"] = pd.to_numeric(h["km_efc"], errors="coerce").astype(int)
    h["is_synth_goes"] = 0
    h["is_densified"] = h.get("is_densified", 0)
    # remove do df linhas que serão cobertas pelo hist (mesmo day×km)
    keys = set(zip(h["day"].astype(str), h["km_efc"].astype(int)))
    base = df.copy()
    base["day"] = pd.to_datetime(base["day"]).dt.normalize()
    if "is_synth_goes" not in base.columns:
        base["is_synth_goes"] = 0
    keep = ~pd.Series(
        list(zip(base["day"].astype(str), base["km_efc"].astype(int))), index=base.index
    ).isin(keys)
    base = base.loc[keep]
    # alinha colunas
    for c in base.columns:
        if c not in h.columns:
            h[c] = np.nan
    for c in h.columns:
        if c not in base.columns:
            base[c] = np.nan
    cols = list(dict.fromkeys(list(base.columns) + list(h.columns)))
    out = pd.concat([base[cols], h[cols]], ignore_index=True, sort=False)
    return out


def _holdout_threshold_table(
    day_df: pd.DataFrame,
    *,
    test_start: str = "2026-04-01",
    t_gate: float = 10.0,
    out_csv: Path | None = None,
    out_png: Path | None = None,
) -> dict:
    """Varre limiares SVM no holdout; escolhe Youden; grava tabela POD/FAR."""
    hold = day_df[day_df["day"] >= pd.Timestamp(test_start)].copy()
    if len(hold) < 5 or hold["label_combo"].nunique() < 2:
        return {"ok": False, "reason": "holdout insuficiente"}
    y = hold["label_combo"].values.astype(int)
    s = hold["svm_score"].values.astype(float)
    precip = pd.to_numeric(hold.get("gate_precip_mm"), errors="coerce").fillna(0).values
    # candidatos: percentis do score + limiar treino se existir
    thr_cand = np.unique(np.quantile(s, np.linspace(0.05, 0.95, 19)))
    if "t_svm" in hold.columns:
        thr_cand = np.unique(np.concatenate([thr_cand, [float(hold["t_svm"].iloc[0])]]))
    rows = []
    best = None
    for thr in thr_cand:
        pred = ((s >= thr) & (precip >= t_gate)).astype(int)
        tp = int(((pred == 1) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        tn = int(((pred == 0) & (y == 0)).sum())
        pod = tp / (tp + fn) if (tp + fn) else 0.0
        far = fp / (tp + fp) if (tp + fp) else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        youden = pod - fpr
        rec = {
            "t_svm": float(thr),
            "t_gate": float(t_gate),
            "pod": pod,
            "far": far,
            "precision": prec,
            "fpr": fpr,
            "youden": youden,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "n": int(len(y)),
            "n_pos": int(y.sum()),
        }
        rows.append(rec)
        if best is None or youden > best["youden"]:
            best = rec
    tab = pd.DataFrame(rows).sort_values("t_svm")
    if out_csv is not None:
        tab.to_csv(out_csv, index=False)
    if out_png is not None and not tab.empty:
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        ax.plot(tab["t_svm"], tab["pod"], "o-", color="#059669", lw=2, label="POD")
        ax.plot(tab["t_svm"], tab["far"], "s-", color="#dc2626", lw=2, label="FAR")
        ax.plot(tab["t_svm"], tab["precision"], "^-", color="#1d4ed8", lw=1.5, label="Precision")
        if best:
            ax.axvline(best["t_svm"], color="#64748b", ls="--", lw=1.2, label=f"Youden t={best['t_svm']:.2f}")
        ax.set_xlabel("Limiar SVM (holdout)")
        ax.set_ylabel("Taxa")
        ax.set_ylim(-0.02, 1.05)
        ax.set_title(
            f"Holdout ≥{test_start} — POD/FAR × limiar (gate≥{t_gate:g} mm)\n"
            f"melhor Youden: POD={best['pod']:.2f} FAR={best['far']:.2f} prec={best['precision']:.2f}"
        )
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        fig.savefig(out_png, dpi=160, bbox_inches="tight")
        plt.close(fig)
    return {
        "ok": True,
        "best_youden": best,
        "n_thresholds": len(tab),
        "table_csv": str(out_csv) if out_csv else None,
        "table_png": str(out_png) if out_png else None,
    }


def _plot_roc(day_df: pd.DataFrame, out: Path, title: str, test_start: str = "2026-04-01") -> dict:
    """ROC no nível dia (protocolo do treino) — verão completo + holdout abr–mai."""
    full = _roc_block(
        day_df["label_combo"].values.astype(int),
        day_df["svm_score"].values.astype(float),
        day_df["pred_cascade"].values.astype(int),
    )
    hold = day_df[day_df["day"] >= pd.Timestamp(test_start)]
    hold_m = None
    if len(hold) >= 5 and hold["label_combo"].nunique() > 1:
        hold_m = _roc_block(
            hold["label_combo"].values.astype(int),
            hold["svm_score"].values.astype(float),
            hold["pred_cascade"].values.astype(int),
        )

    fig, ax = plt.subplots(figsize=(6.4, 5.8))
    ax.plot(
        full["fpr"],
        full["tpr"],
        color="#1d4ed8",
        lw=2.4,
        label=f"Verão completo  AUC={full['roc_auc']:.3f} (n={full['n']}, +={full['n_pos']})",
    )
    if hold_m:
        ax.plot(
            hold_m["fpr"],
            hold_m["tpr"],
            color="#059669",
            lw=2.2,
            label=f"Holdout ≥{test_start}  AUC={hold_m['roc_auc']:.3f} (n={hold_m['n']}, +={hold_m['n_pos']})",
        )
    ax.plot([0, 1], [0, 1], ls="--", color="#94a3b8", lw=1)
    ax.scatter(
        [full["fpr_op"]],
        [full["pod"]],
        s=80,
        color="#dc2626",
        zorder=5,
        label=f"Cascata ops verão (POD={full['pod']:.2f}, FAR={full['far']:.2f})",
    )
    ax.set_xlabel("Taxa de falsos positivos (FPR)")
    ax.set_ylabel("Taxa de verdadeiros positivos (POD)")
    ax.set_title(title)
    ax.legend(loc="lower right", fontsize=8)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return {"summer_day": {k: v for k, v in full.items() if k not in ("fpr", "tpr")}, "holdout_day": (
        {k: v for k, v in hold_m.items() if k not in ("fpr", "tpr")} if hold_m else None
    )}


def _plot_ag(ag: dict, deployed_feats: list[str], out: Path) -> None:
    hist = pd.DataFrame(ag["history"])
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))

    ax = axes[0]
    ax.plot(hist["generation"], hist["best_fitness"], "o-", color="#059669", lw=2, ms=5, label="Melhor da geração")
    ax.plot(hist["generation"], hist["global_best_fitness"], "--", color="#0f766e", lw=1.5, label="Melhor global")
    ax.set_xlabel("Geração")
    ax.set_ylabel("Fitness (ROC-AUC CV)")
    ax.set_title(f"AG — seleção de features (pop={ag['population']}, gen={ag['n_generations']})")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.25)
    ax.set_ylim(0, 1.05)

    ax2 = axes[1]
    # heatmap: deployed features vs AG-selected
    import re

    feat_re = re.compile(r"C(\d{2})_h(\d{2})")
    bands = [f"C{b:02d}" for b in range(1, 17)]
    hours = ["00", "06", "12", "18"]
    mat = np.zeros((len(bands), len(hours)))
    selected = set(ag["selected_features"])
    for f in deployed_feats:
        m = feat_re.match(f)
        if not m:
            continue
        bi, hi = bands.index(f"C{m.group(1)}"), hours.index(m.group(2))
        mat[bi, hi] = 1.0  # deployed
    for f in selected:
        m = feat_re.match(f)
        if not m:
            continue
        bi, hi = bands.index(f"C{m.group(1)}"), hours.index(m.group(2))
        mat[bi, hi] = 2.0 if mat[bi, hi] == 1.0 else 1.5  # overlap / AG-only
    im = ax2.imshow(mat, aspect="auto", cmap="YlGnBu", vmin=0, vmax=2)
    ax2.set_xticks(range(len(hours)))
    ax2.set_xticklabels([f"h{h}" for h in hours])
    ax2.set_yticks(range(len(bands)))
    ax2.set_yticklabels(bands, fontsize=7)
    ax2.set_title("Features: modelo implantado ∩ AG")
    cbar = fig.colorbar(im, ax=ax2, fraction=0.046)
    cbar.set_ticks([0, 1, 1.5, 2])
    cbar.set_ticklabels(["—", "implantado", "AG", "ambos"])
    fig.suptitle(
        f"AG compacto · best AUC-CV={ag['best_fitness']:.3f} · {len(ag['selected_features'])} feats",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _plot_svm(scored: pd.DataFrame, pack: dict, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    ax = axes[0]
    for lab, color, name in [(0, "#64748b", "Negativo"), (1, "#dc2626", "Positivo (EWS∪UNIPLU≥49)")]:
        s = scored.loc[scored["label_combo"] == lab, "svm_score"]
        ax.hist(s, bins=28, alpha=0.65, color=color, label=f"{name} (n={len(s)})", density=True)
    ax.axvline(pack["svm_threshold"], color="#1d4ed8", ls="--", lw=1.5, label=f"t_svm={pack['svm_threshold']:.2f}")
    ax.set_xlabel("SVM decision_function")
    ax.set_ylabel("Densidade")
    ax.set_title("SVM — distribuição do score por classe")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.25)

    ax2 = axes[1]
    # score vs gate precip
    ax2.scatter(
        scored.loc[scored["label_combo"] == 0, "gate_precip_mm"],
        scored.loc[scored["label_combo"] == 0, "svm_score"],
        s=18,
        alpha=0.45,
        c="#64748b",
        label="Neg",
    )
    ax2.scatter(
        scored.loc[scored["label_combo"] == 1, "gate_precip_mm"],
        scored.loc[scored["label_combo"] == 1, "svm_score"],
        s=28,
        alpha=0.75,
        c="#dc2626",
        label="Pos",
    )
    ax2.axhline(pack["svm_threshold"], color="#1d4ed8", ls="--", lw=1)
    ax2.axvline(pack.get("wn2_precip_min_mm", 10.0), color="#059669", ls=":", lw=1.2, label="t_gate")
    ax2.set_xlabel("Precipitação gate (mm)")
    ax2.set_ylabel("SVM score")
    ax2.set_title("SVM — score × gate precip (cascata)")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.25)
    fig.suptitle(
        "SVM pós-VIF (tol=0.2 → VIF≤5) — espaço de decisão + gate precip",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _plot_vif(vif_info: dict, out: Path) -> None:
    """Barras dos VIF finais + contagem de drops."""
    items = sorted(vif_info["vif_final"].items(), key=lambda kv: kv[1], reverse=True)
    names = [k for k, _ in items]
    vals = [v for _, v in items]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [2.2, 1]})
    ax = axes[0]
    colors = ["#dc2626" if v > vif_info["max_vif"] else "#1d4ed8" for v in vals]
    ax.barh(range(len(names)), vals, color=colors, height=0.7)
    ax.axvline(vif_info["max_vif"], color="#dc2626", ls="--", lw=1.4, label=f"VIF máx={vif_info['max_vif']:.0f}")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("VIF")
    ax.set_title(f"VIF final · {len(names)} features mantidas (tol={vif_info['tol']})")
    ax.legend(fontsize=8)
    ax.grid(True, axis="x", alpha=0.25)

    ax2 = axes[1]
    ax2.axis("off")
    dropped = vif_info["dropped"]
    lines = [
        f"tol={vif_info['tol']}  ⇒  VIF≤{vif_info['max_vif']:.0f}",
        f"removidas: {len(dropped)}",
        f"mantidas: {len(names)}",
        "",
        "Top drops (VIF ao remover):",
    ]
    for d in dropped[:12]:
        lines.append(f"  · {d['feature']}: {d['vif']:.1f}")
    if len(dropped) > 12:
        lines.append(f"  … +{len(dropped) - 12} outras")
    ax2.text(0.02, 0.98, "\n".join(lines), va="top", ha="left", fontsize=9, family="monospace",
             transform=ax2.transAxes)
    fig.suptitle("Filtro VIF antes do SVM", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("/opt/cursor/artifacts/raijin_eval"))
    ap.add_argument("--keep-frac", type=float, default=0.75, help="Fração dos negativos a manter (~3/4)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--uniplu-catalog",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "uniplu_efc_catalog_r20km.csv",
    )
    ap.add_argument("--ag-gen", type=int, default=12)
    ap.add_argument("--ag-pop", type=int, default=12)
    ap.add_argument(
        "--vif-tol",
        type=float,
        default=0.2,
        help="Tolerância VIF (0.2 ⇒ remove features com VIF>5 antes do SVM)",
    )
    ap.add_argument(
        "--synth-factor",
        type=float,
        default=0.0,
        help="Sintéticos por autovalores/autovetores: 1.0 dobra cada classe no treino",
    )
    ap.add_argument(
        "--synth-mode",
        choices=("both", "pos", "neg"),
        default="both",
        help="Quais classes aumentar com sintéticos eigen",
    )
    ap.add_argument(
        "--dense-km-step",
        type=int,
        default=0,
        help="Densifica malha de km (ex.: 5) interpolando GOES/ECMWF entre âncoras",
    )
    ap.add_argument(
        "--train-mult",
        type=float,
        default=0.0,
        help="Alvo de volume no fit vs N linhas originais (ex.: 10 ⇒ ~10× com densify+eigen)",
    )
    ap.add_argument(
        "--roc-on",
        choices=("anchors", "all"),
        default="anchors",
        help="ROC só nos km originais (anchors) ou em toda malha densificada",
    )
    ap.add_argument(
        "--extend-start",
        type=str,
        default="",
        help="Início do calendário extra de rótulos UNIPLU/MERGE (ex.: 2024-10-01); "
        "dias sem GOES ganham features sintéticas eigen",
    )
    ap.add_argument(
        "--balance-per-class",
        type=int,
        default=0,
        help="Força ~50/50 no fit: N positivos e N negativos (reais+eigen). Ex.: 2000",
    )
    ap.add_argument(
        "--hist-goes",
        type=Path,
        default=None,
        help="CSV de features GOES históricas reais (build_hist_goes_features.py)",
    )
    ap.add_argument(
        "--holdout-start",
        type=str,
        default="2026-04-01",
        help="Início do holdout para ROC e tabela de limiares",
    )
    args = ap.parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path("/tmp/raijin_eval/data")
    work.mkdir(parents=True, exist_ok=True)

    print("1) Baixando features + modelo implantado…")
    feat_path = _download(FEATURES_BLOB, work / "features_cascade_v2.csv")
    model_path = _download(MODEL_BLOB, work / "cascade_v2_model.joblib")
    pack_deployed = joblib.load(model_path)
    df = pd.read_csv(feat_path, parse_dates=["day"])
    df["day"] = pd.to_datetime(df["day"]).dt.normalize()
    df = df[df["day"].dt.month.isin(SUMMER_MONTHS)].copy()
    df["is_synth_goes"] = 0
    d0, d1 = df["day"].min().date(), df["day"].max().date()
    print(f"   Período features verão: {d0} → {d1} · {len(df)} linhas · {df['km_efc'].nunique()} km")

    # GOES histórico real (substitui synth nos dias cobertos)
    hist_path = args.hist_goes
    if hist_path is None:
        # defaults locais / artefato
        for cand in (
            Path("/opt/cursor/artifacts/raijin_eval/features_goes_hist_202410_202512.csv"),
            Path.home() / "raijin_eval" / "features_goes_hist_202410_202512.csv",
            work / "features_goes_hist_202410_202512.csv",
        ):
            if cand.is_file() and cand.stat().st_size > 0:
                hist_path = cand
                break
    if hist_path and hist_path.is_file():
        print(f"1b) Mesclando GOES histórico real ← {hist_path}")
        hist = pd.read_csv(hist_path, parse_dates=["day"])
        before = len(df)
        df = _merge_hist_goes(df, hist)
        d0, d1 = df["day"].min().date(), df["day"].max().date()
        print(
            f"   {before} → {len(df)} linhas · dias={df['day'].nunique()} · "
            f"período {d0}→{d1} · hist_rows={len(hist)}"
        )
    else:
        print("1b) Sem --hist-goes (só cascade_v2 + synth se extend)")

    print("2) Rótulos EWS + UNIPLU (+ MERGE se extend)…")
    df["ews_mm"] = pd.to_numeric(df.get("ews_mm"), errors="coerce")
    df["label_ews"] = (df["ews_mm"].fillna(0) >= THRESHOLD_MM).astype(int)
    kms0 = sorted(df["km_efc"].dropna().astype(int).unique())
    extend_d0 = args.extend_start.strip() or str(d0)
    uni = _load_uniplu_near_kms(
        kms=kms0,
        d0=extend_d0,
        d1=str(d1),
        catalog=args.uniplu_catalog,
    )
    if not uni.empty:
        # evita colunas duplicadas se já veio do hist merge
        if "uniplu_mm" in df.columns:
            df = df.drop(columns=["uniplu_mm"])
        df = df.merge(uni, on=["day", "km_efc"], how="left")
    else:
        if "uniplu_mm" not in df.columns:
            df["uniplu_mm"] = np.nan
    df["label_uniplu"] = (pd.to_numeric(df["uniplu_mm"], errors="coerce").fillna(0) >= THRESHOLD_MM).astype(int)
    df["label_combo"] = ((df["label_ews"] == 1) | (df["label_uniplu"] == 1)).astype(int)
    n_u = int(df["uniplu_mm"].notna().sum())
    print(
        f"   EWS+={df['label_ews'].sum()} · UNIPLU rows matched={n_u} · "
        f"UNIPLU+={df['label_uniplu'].sum()} · combo+={df['label_combo'].sum()} · "
        f"dias evento={int(df.groupby('day')['label_combo'].max().sum())}"
    )
    n_base = len(df)

    # pool de features GOES/ECMWF para extensão / densify
    feat_pool = [
        c
        for c in df.columns
        if (c.startswith("C") and "_h" in c) or c.startswith("bq_ecmwf_")
    ]
    feat_pool = list(dict.fromkeys(list(pack_deployed["features"]) + feat_pool))
    feat_pool = [c for c in feat_pool if c in df.columns]

    if args.extend_start.strip():
        print(f"2b) Estender calendário {args.extend_start}→{d1} (rótulos reais + GOES synth)…")
        merge = _load_merge_km_daily(kms0, args.extend_start, str(d1))
        # UNIPLU completo na janela extend (já carregado acima em `uni`)
        before_ext = len(df)
        df = _extend_calendar_synth_goes(
            df, feat_pool, uni=uni, merge=merge, seed=args.seed
        )
        n_ev_days = (
            df.groupby("day")["label_combo"].max().sum() if len(df) else 0
        )
        print(
            f"   {before_ext} → {len(df)} linhas · dias={df['day'].nunique()} · "
            f"dias evento={int(n_ev_days)} · synth_goes={int(df['is_synth_goes'].sum())}"
        )
        n_base_goes = n_base
        n_base = len(df)
    else:
        n_base_goes = n_base

    print(
        f"3) Densificar malha de km"
        + (f" (step={args.dense_km_step})" if args.dense_km_step > 0 else " (off)")
        + "…"
    )
    before_dense = len(df)
    df_d = _densify_km_grid(df, step=args.dense_km_step)
    print(
        f"   {before_dense} → {len(df_d)} linhas · km={df_d['km_efc'].nunique()} "
        f"(densificadas={int(df_d['is_densified'].sum())})"
    )

    print(f"4) Subsample keep_frac={args.keep_frac} (negativos)…")
    before = len(df_d)
    df_s = _subsample(df_d, keep_frac=args.keep_frac, seed=args.seed)
    print(f"   {before} → {len(df_s)} linhas (pos={df_s['label_combo'].sum()})")

    train_end = "2026-03-31"
    print(f"5) Filtro VIF (tol={args.vif_tol} → VIF≤{1.0/args.vif_tol:.1f}) antes do SVM…")
    cand = [c for c in df_s.columns if (c.startswith("C") and "_h" in c) or c.startswith("bq_ecmwf_")]
    # prioriza o pool do modelo implantado + extras GOES/ECMWF presentes
    pool = list(dict.fromkeys(list(pack_deployed["features"]) + cand))
    pool = [c for c in pool if c in df_s.columns]
    # VIF só na janela de treino (sem vazar abr–mai)
    df_vif = df_s[df_s["day"] <= pd.Timestamp(train_end)].copy()
    if len(df_vif) < 30:
        df_vif = df_s
    vif_info = _vif_filter(df_vif, pool, tol=args.vif_tol, min_features=3)
    print(
        f"   pool={len(pool)} → kept={len(vif_info['kept'])} · "
        f"dropped={len(vif_info['dropped'])} · max_vif_final="
        f"{max(vif_info['vif_final'].values()) if vif_info['vif_final'] else float('nan'):.2f}"
        f" · VIF fit≤{train_end}"
    )
    _plot_vif(vif_info, out_dir / "fig_vif_filter.png")

    # volume alvo no fit: densify já expandiu; eigen completa até --train-mult
    synth_factor = float(args.synth_factor)
    n_train_est = int((df_s["day"] <= pd.Timestamp(train_end)).sum())
    if args.train_mult and args.train_mult > 1:
        auto_f = _synth_factor_for_mult(
            n_train=n_train_est, n_base=n_base, target_mult=args.train_mult, mode=args.synth_mode
        )
        # se densify já passou do alvo, eigen mínimo; senão completa
        synth_factor = max(synth_factor, auto_f)
        print(
            f"   train-mult={args.train_mult:g}× (base={n_base}) → "
            f"synth-factor auto={synth_factor:.2f} (treino≈{n_train_est})"
        )

    print(
        f"6) Treinar SVM pós-VIF + gate"
        + (f" + sintéticos eigen×{synth_factor:.2f} ({args.synth_mode})" if synth_factor > 0 else "")
        + "…"
    )
    t_gate = float(pack_deployed.get("wn2_precip_min_mm", 10.0))
    scored, pack = _fit_svm_cascade(
        df_s,
        vif_info["kept"],
        t_gate=t_gate,
        seed=args.seed,
        train_end=train_end,
        synth_factor=0.0 if args.balance_per_class > 0 else synth_factor,
        synth_mode=args.synth_mode,
        synth_plot=(out_dir / "fig_eigen_synth.png")
        if (synth_factor > 0 or args.balance_per_class > 0)
        else None,
        balance_per_class=args.balance_per_class,
    )
    print(
        f"   treino rows={pack['n_train']} → fit={pack['n_train_fit']} "
        f"(pos={pack.get('n_train_pos_fit')} neg={pack.get('n_train_neg_fit')}; "
        f"×{pack['n_train_fit'] / max(n_base, 1):.1f} vs base {n_base})"
    )

    print("7) Agregar nível dia + figuras ROC/SVM…")
    scored_roc = scored
    if args.roc_on != "all" and "is_densified" in scored_roc.columns:
        scored_roc = scored_roc[scored_roc["is_densified"] == 0]
    if "is_synth_goes" in scored_roc.columns:
        scored_roc = scored_roc[scored_roc["is_synth_goes"] == 0]
    day_df = _aggregate_day(scored_roc)
    print(
        f"   dias={len(day_df)} · positivos={int(day_df['label_combo'].sum())} "
        f"(roc_on={args.roc_on})"
    )
    metrics = _plot_roc(
        day_df,
        out_dir / "fig_roc_auc_verao.png",
        title=(
            f"ROC (nível dia) — VIF tol={args.vif_tol}→SVM"
            + (f"+eigen×{synth_factor:.1f}" if synth_factor > 0 else "")
            + (f"+dense{args.dense_km_step}" if args.dense_km_step > 0 else "")
            + f" · {d0}→{d1}\n"
            f"rótulo EWS∪UNIPLU ≥{THRESHOLD_MM:g} mm · "
            f"fit×{pack['n_train_fit'] / max(n_base, 1):.1f} · subsample neg={args.keep_frac:.0%}"
        ),
        test_start=args.holdout_start,
    )
    _plot_svm(day_df, pack, out_dir / "fig_svm_decision.png")

    print("7b) Tabela POD/FAR × limiar no holdout…")
    thr_info = _holdout_threshold_table(
        day_df,
        test_start=args.holdout_start,
        t_gate=t_gate,
        out_csv=out_dir / "holdout_threshold_table.csv",
        out_png=out_dir / "fig_holdout_thresholds.png",
    )
    if thr_info.get("ok") and thr_info.get("best_youden"):
        b = thr_info["best_youden"]
        print(
            f"   Youden holdout: t_svm={b['t_svm']:.3f} · "
            f"POD={b['pod']:.2f} FAR={b['far']:.2f} prec={b['precision']:.2f}"
        )
        metrics["holdout_best_youden"] = b
    else:
        print(f"   holdout limiar: {thr_info}")

    print("8) AG compacto sobre pool pós-VIF…")
    ag = _run_ag_compact(
        df_s,
        vif_info["kept"],
        GAConfig(population=args.ag_pop, n_generations=args.ag_gen, random_seed=args.seed),
    )
    _plot_ag(ag, list(pack_deployed["features"]), out_dir / "fig_ag_fitness.png")

    summary = {
        "period": {"start": str(d0), "end": str(d1)},
        "model": (
            f"SVM pós-VIF (tol={args.vif_tol}, VIF≤{1.0/args.vif_tol:.1f}) + gate precip"
            + (f" + dense_km={args.dense_km_step}" if args.dense_km_step > 0 else "")
            + (f" + eigen-synth×{synth_factor:.2f} ({args.synth_mode})" if synth_factor > 0 else "")
        ),
        "base_deployed": "cascade_v2 (referência de features/gate)",
        "label": f"EWS∪UNIPLU ≥ {THRESHOLD_MM} mm/24h",
        "period_note": (
            "Features GOES disponíveis só 2026-01-01→2026-05-20 (13 km âncora). "
            "Expansão de volume = densificar km + sintéticos eigen (não há GOES histórico "
            "extra no GCS para alongar o calendário)."
        ),
        "uniplu_note": (
            "UNIPLU no BQ termina em 2025-12-31; overlap com features GOES "
            f"({d0}→{d1}) é limitado — rótulo combo ≈ EWS neste verão."
        ),
        "vif": {
            "tol": vif_info["tol"],
            "max_vif": vif_info["max_vif"],
            "n_pool": len(pool),
            "n_kept": len(vif_info["kept"]),
            "n_dropped": len(vif_info["dropped"]),
            "kept": vif_info["kept"],
            "dropped": vif_info["dropped"],
            "vif_final": vif_info["vif_final"],
        },
        "expand": {
            "n_base": n_base,
            "dense_km_step": args.dense_km_step,
            "n_after_densify": before,
            "train_mult_target": args.train_mult,
            "n_train_fit": pack.get("n_train_fit"),
            "fit_vs_base": round(pack["n_train_fit"] / max(n_base, 1), 2),
            "roc_on": args.roc_on,
        },
        "synth_eigen": {
            "factor": synth_factor,
            "mode": args.synth_mode,
            "n_train_real": pack.get("n_train"),
            "n_train_fit": pack.get("n_train_fit"),
            "detail": pack.get("synth"),
        },
        "n_before_subsample": before,
        "n_after_subsample": len(df_s),
        "keep_frac_negatives": args.keep_frac,
        "metrics": metrics,
        "ag": {
            "best_fitness_auc_cv": ag["best_fitness"],
            "n_selected": len(ag["selected_features"]),
            "selected_features": ag["selected_features"],
            "overlap_with_deployed": sorted(
                set(ag["selected_features"]) & set(pack_deployed["features"])
            ),
            "generations": ag["n_generations"],
            "population": ag["population"],
        },
        "deployed_features": list(pack_deployed["features"]),
        "svm_features_post_vif": list(pack["features"]),
        "holdout_thresholds": thr_info,
        "figures": [
            "fig_roc_auc_verao.png",
            "fig_ag_fitness.png",
            "fig_svm_decision.png",
            "fig_vif_filter.png",
            "fig_holdout_thresholds.png",
            "holdout_threshold_table.csv",
            *(["fig_eigen_synth.png"] if (synth_factor > 0 or args.balance_per_class > 0) else []),
        ],
    }
    (out_dir / "eval_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    scored.to_csv(out_dir / "scored_summer_subsample.csv", index=False)
    print(json.dumps(summary["metrics"], indent=2))
    print(f"Figuras → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
