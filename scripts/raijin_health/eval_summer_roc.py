#!/usr/bin/env python3
"""
Avaliação matemática do modelo implantado (cascade_v2) no último verão
com features GOES + rótulos EWS/UNIPLU — sem deploy.

- Features: gs://…/cascade_v2/features_cascade_v2.csv (2026-01-01 → 2026-05-20)
- Rótulo EWS: ews_mm ≥ 49 (já no CSV)
- Rótulo UNIPLU: precip_mm ≥ 49 nas estações do catálogo EFC (r≤20 km),
  mapeadas ao km_efc; UNIPLU no BQ vai até 2025-12-31 (overlap parcial)
- Subsample ~3/4 do espaço amostral (mantém todos os positivos)
- Figuras: ROC/AUC, AG (fitness), SVM (scores)

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


def _score_deployed(df: pd.DataFrame, pack: dict) -> pd.DataFrame:
    feat_cols = list(pack["features"])
    work = df.copy()
    for c in feat_cols:
        if c not in work.columns:
            work[c] = np.nan
        work[c] = pd.to_numeric(work[c], errors="coerce")
        med = work[c].median(skipna=True)
        work[c] = work[c].fillna(0.0 if pd.isna(med) else float(med))
    X = pack["scaler"].transform(work[feat_cols].values.astype(float))
    work["svm_score"] = pack["model"].decision_function(X)
    t_svm = float(pack["svm_threshold"])
    t_gate = float(pack.get("wn2_precip_min_mm", 10.0))
    # Gate: WN2 se houver; senão MERGE/ECMWF do CSV
    precip = pd.to_numeric(work.get("wn2_precip_mm"), errors="coerce")
    if "merge_mm_24h" in work.columns:
        precip = precip.fillna(pd.to_numeric(work["merge_mm_24h"], errors="coerce"))
    if "bq_ecmwf_precip_24h_mm" in work.columns:
        precip = precip.fillna(pd.to_numeric(work["bq_ecmwf_precip_24h_mm"], errors="coerce"))
    precip = precip.fillna(0.0)
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
    fig.suptitle("Modelo implantado cascade_v2 — espaço de decisão SVM", fontsize=11, fontweight="bold")
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
        default=Path("/tmp/zentra_monitor_work/data/merge_uniplu_efc/uniplu_efc_catalog_r20km.csv"),
    )
    ap.add_argument("--ag-gen", type=int, default=12)
    ap.add_argument("--ag-pop", type=int, default=12)
    args = ap.parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path("/tmp/raijin_eval/data")
    work.mkdir(parents=True, exist_ok=True)

    print("1) Baixando features + modelo implantado…")
    feat_path = _download(FEATURES_BLOB, work / "features_cascade_v2.csv")
    model_path = _download(MODEL_BLOB, work / "cascade_v2_model.joblib")
    pack = joblib.load(model_path)
    df = pd.read_csv(feat_path, parse_dates=["day"])
    df["day"] = pd.to_datetime(df["day"]).dt.normalize()
    df = df[df["day"].dt.month.isin(SUMMER_MONTHS)].copy()
    d0, d1 = df["day"].min().date(), df["day"].max().date()
    print(f"   Período features verão: {d0} → {d1} · {len(df)} linhas · {df['km_efc'].nunique()} km")

    print("2) Rótulos EWS + UNIPLU…")
    df["ews_mm"] = pd.to_numeric(df.get("ews_mm"), errors="coerce")
    df["label_ews"] = (df["ews_mm"].fillna(0) >= THRESHOLD_MM).astype(int)
    # UNIPLU: janela estendida cobrindo fim de 2025 + features 2026
    uni = _load_uniplu_near_kms(
        kms=sorted(df["km_efc"].dropna().astype(int).unique()),
        # maior janela chuvosa com UNIPLU disponível no BQ (até 2025-12-31)
        d0="2025-01-01",
        d1=str(d1),
        catalog=args.uniplu_catalog,
    )
    if not uni.empty:
        df = df.merge(uni, on=["day", "km_efc"], how="left")
    else:
        df["uniplu_mm"] = np.nan
    df["label_uniplu"] = (pd.to_numeric(df["uniplu_mm"], errors="coerce").fillna(0) >= THRESHOLD_MM).astype(int)
    df["label_combo"] = ((df["label_ews"] == 1) | (df["label_uniplu"] == 1)).astype(int)
    n_u = int(df["uniplu_mm"].notna().sum())
    print(
        f"   EWS+={df['label_ews'].sum()} · UNIPLU rows matched={n_u} · "
        f"UNIPLU+={df['label_uniplu'].sum()} · combo+={df['label_combo'].sum()}"
    )

    print(f"3) Subsample keep_frac={args.keep_frac} (negativos)…")
    before = len(df)
    df_s = _subsample(df, keep_frac=args.keep_frac, seed=args.seed)
    print(f"   {before} → {len(df_s)} linhas (pos={df_s['label_combo'].sum()})")

    print("4) Score com modelo implantado…")
    scored = _score_deployed(df_s, pack)

    print("5) Agregar nível dia (protocolo do treino) + figuras…")
    day_df = _aggregate_day(scored)
    print(f"   dias={len(day_df)} · positivos={int(day_df['label_combo'].sum())}")
    metrics = _plot_roc(
        day_df,
        out_dir / "fig_roc_auc_verao.png",
        title=(
            f"ROC (nível dia) — cascade_v2 implantado · {d0}→{d1}\n"
            f"rótulo EWS∪UNIPLU ≥{THRESHOLD_MM:g} mm "
            f"(UNIPLU BQ ≤2025-12-31 → combo≈EWS) · subsample neg={args.keep_frac:.0%}"
        ),
    )
    _plot_svm(day_df, pack, out_dir / "fig_svm_decision.png")

    print("6) AG compacto (figura)…")
    # candidate feature pool = all GOES+bq cols present (AG explores ~35% mask ≈ 3/4 reduction of active set)
    cand = [c for c in df_s.columns if (c.startswith("C") and "_h" in c) or c.startswith("bq_ecmwf_")]
    ag = _run_ag_compact(
        df_s,
        cand,
        GAConfig(population=args.ag_pop, n_generations=args.ag_gen, random_seed=args.seed),
    )
    _plot_ag(ag, list(pack["features"]), out_dir / "fig_ag_fitness.png")

    summary = {
        "period": {"start": str(d0), "end": str(d1)},
        "model": "cascade_v2 (implantado)",
        "label": f"EWS∪UNIPLU ≥ {THRESHOLD_MM} mm/24h",
        "uniplu_note": (
            "UNIPLU no BQ termina em 2025-12-31; overlap com features GOES "
            f"({d0}→{d1}) é limitado — rótulo combo ≈ EWS neste verão."
        ),
        "n_before_subsample": before,
        "n_after_subsample": len(df_s),
        "keep_frac_negatives": args.keep_frac,
        "metrics": metrics,
        "ag": {
            "best_fitness_auc_cv": ag["best_fitness"],
            "n_selected": len(ag["selected_features"]),
            "selected_features": ag["selected_features"],
            "overlap_with_deployed": sorted(set(ag["selected_features"]) & set(pack["features"])),
            "generations": ag["n_generations"],
            "population": ag["population"],
        },
        "deployed_features": list(pack["features"]),
        "figures": [
            "fig_roc_auc_verao.png",
            "fig_ag_fitness.png",
            "fig_svm_decision.png",
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
