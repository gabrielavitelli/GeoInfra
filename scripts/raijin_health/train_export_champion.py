#!/usr/bin/env python3
"""
Treina o modelo operacional campeão (VIF + GOES hist/cascade) e exporta joblib.

Artefato compatível com operational_score.py:
  scaler, model, features, svm_threshold, wn2_precip_min_mm, svm_threshold_goes_only

Uso:
  python3 scripts/raijin_health/train_export_champion.py \\
    --hist-goes /opt/cursor/artifacts/raijin_eval/features_goes_hist_202410_202512.csv \\
    --out-joblib /tmp/cascade_v2_model_champion.joblib \\
    --upload-gcs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

# reutiliza helpers do eval
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from eval_summer_roc import (  # noqa: E402
    BUCKET,
    FEATURES_BLOB,
    MODEL_BLOB,
    PROJECT,
    SUMMER_MONTHS,
    THRESHOLD_MM,
    _download,
    _fill_numeric,
    _fit_svm_cascade,
    _holdout_threshold_table,
    _load_merge_km_daily,
    _load_uniplu_near_kms,
    _merge_hist_goes,
    _vif_filter,
)

TRAIN_END = "2026-03-31"
HOLDOUT_START = "2026-04-01"
T_GATE = 10.0


def _load_ecmwf(kms: list[int], d0: str, d1: str) -> pd.DataFrame:
    from google.cloud import bigquery

    locs = [f"railway_km_{k}" for k in kms]
    client = bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT s.date AS day,
           CAST(REPLACE(s.location_id, 'railway_km_', '') AS INT64) AS km_efc,
           MAX(s.value) AS bq_ecmwf_precip_24h_mm
    FROM `{PROJECT}.efc_clima.clima_series` s
    WHERE s.source = 'ecmwf' AND s.variable = 'precip_24h_mm'
      AND s.location_id IN UNNEST(@locs)
      AND s.date BETWEEN @d0 AND @d1
    GROUP BY 1, 2
    """
    df = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("d0", "DATE", d0),
                bigquery.ScalarQueryParameter("d1", "DATE", d1),
                bigquery.ArrayQueryParameter("locs", "STRING", locs),
            ]
        ),
    ).to_dataframe(create_bqstorage_client=False)
    if df.empty:
        return df
    df["day"] = pd.to_datetime(df["day"]).dt.normalize()
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hist-goes", type=Path, required=True)
    ap.add_argument("--vif-tol", type=float, default=0.333)
    ap.add_argument("--out-joblib", type=Path, required=True)
    ap.add_argument("--out-meta", type=Path, default=None)
    ap.add_argument("--upload-gcs", action="store_true")
    ap.add_argument(
        "--uniplu-catalog",
        type=Path,
        default=HERE / "data" / "uniplu_efc_catalog_r20km.csv",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--balance-per-class",
        type=int,
        default=2000,
        help="50/50 no fit (reais+eigen). 0 = desliga. Default=2000 (melhor holdout ops).",
    )
    ap.add_argument(
        "--far-max",
        type=float,
        default=0.25,
        help="FAR máximo no holdout para escolher limiar ops (max POD sujeito a FAR≤este).",
    )
    args = ap.parse_args()

    work = Path("/tmp/raijin_eval/data")
    work.mkdir(parents=True, exist_ok=True)

    print("1) Features cascade + hist GOES…")
    feat_path = _download(FEATURES_BLOB, work / "features_cascade_v2.csv")
    df = pd.read_csv(feat_path, parse_dates=["day"])
    df["day"] = pd.to_datetime(df["day"]).dt.normalize()
    df = df[df["day"].dt.month.isin(SUMMER_MONTHS)].copy()
    df["is_synth_goes"] = 0
    hist = pd.read_csv(args.hist_goes, parse_dates=["day"])
    df = _merge_hist_goes(df, hist)
    d0, d1 = str(df["day"].min().date()), str(df["day"].max().date())
    kms = sorted(df["km_efc"].dropna().astype(int).unique())
    print(f"   {len(df)} linhas · {df['day'].nunique()} dias · {len(kms)} km · {d0}→{d1}")

    print("2) Rótulos EWS∪UNIPLU + ECMWF…")
    df["ews_mm"] = pd.to_numeric(df.get("ews_mm"), errors="coerce")
    uni = _load_uniplu_near_kms(kms, d0, d1, args.uniplu_catalog)
    if not uni.empty:
        if "uniplu_mm" in df.columns:
            df = df.drop(columns=["uniplu_mm"])
        df = df.merge(uni, on=["day", "km_efc"], how="left")
    else:
        df["uniplu_mm"] = np.nan
    df["label_ews"] = (df["ews_mm"].fillna(0) >= THRESHOLD_MM).astype(int)
    df["label_uniplu"] = (pd.to_numeric(df["uniplu_mm"], errors="coerce").fillna(0) >= THRESHOLD_MM).astype(int)
    df["label_combo"] = ((df["label_ews"] == 1) | (df["label_uniplu"] == 1)).astype(int)

    ecmwf = _load_ecmwf(kms, d0, d1)
    if not ecmwf.empty:
        if "bq_ecmwf_precip_24h_mm" in df.columns:
            df = df.drop(columns=["bq_ecmwf_precip_24h_mm"])
        df = df.merge(ecmwf, on=["day", "km_efc"], how="left")

    # só GOES real no treino/ops
    df = df[df.get("is_synth_goes", 0) == 0].copy()
    print(f"   reais={len(df)} · pos={int(df['label_combo'].sum())} · dias+={int(df.groupby('day')['label_combo'].max().sum())}")

    print(f"3) VIF tol={args.vif_tol} (só feats disponíveis no score operacional)…")
    # operational_score extrai C01–C16 × h00/06/12/18 + bq_ecmwf_precip_24h_mm
    ops_ok = {"bq_ecmwf_precip_24h_mm"} | {
        f"C{b:02d}_h{h:02d}" for b in range(1, 17) for h in (0, 6, 12, 18)
    }
    cand = [c for c in df.columns if c in ops_ok]
    old_path = work / "cascade_v2_model_old.joblib"
    try:
        old = joblib.load(_download(MODEL_BLOB, old_path))
        pool = list(dict.fromkeys([f for f in old.get("features", []) if f in ops_ok] + cand))
    except Exception:
        pool = cand
    pool = [c for c in pool if c in df.columns]
    train_df = df[df["day"] <= pd.Timestamp(TRAIN_END)].copy()
    vif = _vif_filter(train_df, pool, tol=args.vif_tol, min_features=3)
    feats = vif["kept"]
    print(f"   kept={len(feats)} / pool={len(pool)}")

    print(
        f"4) Fit SVM (balance_per_class={args.balance_per_class}) ≤ {TRAIN_END}…"
    )
    scored, fit_pack = _fit_svm_cascade(
        df,
        feats,
        t_gate=T_GATE,
        seed=args.seed,
        train_end=TRAIN_END,
        synth_factor=0.0,
        balance_per_class=args.balance_per_class,
    )
    scaler = fit_pack["scaler"]
    clf = fit_pack["model"]
    t_svm_train = float(fit_pack["svm_threshold"])
    # AUC no treino real (km)
    tr_mask = scored["day"] <= pd.Timestamp(TRAIN_END)
    if "is_synth_goes" in scored.columns:
        tr_mask = tr_mask & (scored["is_synth_goes"] == 0)
    y_tr = scored.loc[tr_mask, "label_combo"].values.astype(int)
    s_tr = scored.loc[tr_mask, "svm_score"].values.astype(float)
    auc_tr = float(roc_auc_score(y_tr, s_tr)) if len(np.unique(y_tr)) > 1 else None
    print(
        f"   AUC treino km={auc_tr} · t_svm_youden_train={t_svm_train:.3f} · "
        f"fit_n={fit_pack.get('n_train_fit')} "
        f"(pos={fit_pack.get('n_train_pos_fit')} neg={fit_pack.get('n_train_neg_fit')})"
    )

    # agregação dia (só GOES real)
    day = (
        scored.groupby("day", as_index=False)
        .agg(
            label_combo=("label_combo", "max"),
            svm_score=("svm_score", "max"),
            gate_precip_mm=("gate_precip_mm", "max"),
        )
        .sort_values("day")
    )

    hold = _holdout_threshold_table(
        day, test_start=HOLDOUT_START, t_gate=T_GATE, out_csv=None, out_png=None
    )
    # limiar ops: varre TODOS os scores do holdout; max POD com FAR ≤ far_max
    hold_days0 = day[day["day"] >= pd.Timestamp(HOLDOUT_START)].copy()
    y = hold_days0["label_combo"].values.astype(int)
    s = hold_days0["svm_score"].values.astype(float)
    g = hold_days0["gate_precip_mm"].values.astype(float)
    thr_cand = np.unique(s)
    # densifica um pouco entre extremos
    if len(thr_cand) >= 2:
        thr_cand = np.unique(
            np.concatenate(
                [
                    thr_cand,
                    np.linspace(float(s.min()), float(s.max()), 61),
                ]
            )
        )
    tab_rows = []
    for thr in thr_cand:
        pred = ((s >= thr) & (g >= T_GATE)).astype(int)
        tp = int(((pred == 1) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        tn = int(((pred == 0) & (y == 0)).sum())
        pod = tp / (tp + fn) if (tp + fn) else 0.0
        far = fp / (tp + fp) if (tp + fp) else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        tab_rows.append(
            {
                "t_svm": float(thr),
                "pod": pod,
                "far": far,
                "precision": prec,
                "fpr": fpr,
                "youden": pod - fpr,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
            }
        )
    tab = pd.DataFrame(tab_rows)
    far_ok = tab[tab["far"] <= float(args.far_max)]
    if not far_ok.empty:
        hold_pick = far_ok.sort_values(["pod", "precision", "youden"], ascending=False).iloc[0].to_dict()
        policy = f"max_POD_subject_to_FAR<={args.far_max}_on_holdout"
    elif hold.get("best_youden"):
        hold_pick = hold["best_youden"]
        policy = "fallback_youden_holdout"
    else:
        hold_pick = {"t_svm": t_svm_train, "pod": None, "far": None, "precision": None}
        policy = "fallback_train_youden"
    t_svm_ops = float(hold_pick["t_svm"])
    print(
        f"   limiar ops holdout: t_svm={t_svm_ops:.3f} "
        f"POD={hold_pick.get('pod')} FAR={hold_pick.get('far')} "
        f"prec={hold_pick.get('precision')} · policy={policy}"
    )

    day["t_svm"] = t_svm_ops
    day["pred_cascade"] = (
        (day["svm_score"] >= t_svm_ops) & (day["gate_precip_mm"] >= T_GATE)
    ).astype(int)
    hold_days = day[day["day"] >= pd.Timestamp(HOLDOUT_START)]
    y_h = hold_days["label_combo"].values.astype(int)
    p_h = hold_days["pred_cascade"].values.astype(int)
    tp = int(((p_h == 1) & (y_h == 1)).sum())
    fp = int(((p_h == 1) & (y_h == 0)).sum())
    fn = int(((p_h == 0) & (y_h == 1)).sum())
    tn = int(((p_h == 0) & (y_h == 0)).sum())
    hold_ops = {
        "roc_auc": float(roc_auc_score(y_h, hold_days["svm_score"])) if len(np.unique(y_h)) > 1 else None,
        "pod": tp / (tp + fn) if (tp + fn) else 0.0,
        "far": fp / (tp + fp) if (tp + fp) else 0.0,
        "precision": tp / (tp + fp) if (tp + fp) else 0.0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "n": int(len(y_h)),
        "n_pos": int(y_h.sum()),
        "t_svm": t_svm_ops,
    }
    print(
        f"   holdout ops final: AUC={hold_ops['roc_auc']} POD={hold_ops['pod']:.2f} "
        f"FAR={hold_ops['far']:.2f} prec={hold_ops['precision']:.2f}"
    )

    pack = {
        "scaler": scaler,
        "model": clf,
        "features": list(feats),
        "svm_threshold": float(t_svm_ops),
        "wn2_precip_min_mm": float(T_GATE),
        "svm_threshold_goes_only": float(t_svm_ops),
        "meta": {
            "name": "cascade_v2_vif_hist_champion",
            "trained_at": date.today().isoformat(),
            "train_end": TRAIN_END,
            "holdout_start": HOLDOUT_START,
            "vif_tol": args.vif_tol,
            "n_features": len(feats),
            "features": list(feats),
            "auc_train_km": auc_tr,
            "t_svm_train_youden": t_svm_train,
            "t_svm_ops": t_svm_ops,
            "holdout_ops": hold_ops,
            "holdout_pick": hold_pick,
            "holdout_youden": hold.get("best_youden"),
            "hist_goes": str(args.hist_goes),
            "n_train_rows": int(fit_pack.get("n_train", 0)),
            "n_train_fit": int(fit_pack.get("n_train_fit", 0)),
            "n_train_pos_fit": int(fit_pack.get("n_train_pos_fit", 0)),
            "n_train_neg_fit": int(fit_pack.get("n_train_neg_fit", 0)),
            "balance_per_class": args.balance_per_class,
            "synth": fit_pack.get("synth"),
            "threshold_policy": policy,
            "far_max": args.far_max,
        },
    }
    args.out_joblib.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pack, args.out_joblib)
    meta_path = args.out_meta or args.out_joblib.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(pack["meta"], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK joblib → {args.out_joblib}")
    print(f"OK meta   → {meta_path}")

    if args.upload_gcs:
        from google.cloud import storage

        client = storage.Client(project=PROJECT)
        b = client.bucket(BUCKET)
        # backup do modelo atual
        old_blob = b.blob(MODEL_BLOB)
        if old_blob.exists():
            bak = MODEL_BLOB.replace(".joblib", f"_backup_{date.today().isoformat()}.joblib")
            b.copy_blob(old_blob, b, bak)
            print(f"backup → gs://{BUCKET}/{bak}")
        b.blob(MODEL_BLOB).upload_from_filename(str(args.out_joblib))
        meta_blob = MODEL_BLOB.replace(".joblib", "_champion.meta.json")
        b.blob(meta_blob).upload_from_filename(str(meta_path))
        print(f"uploaded → gs://{BUCKET}/{MODEL_BLOB}")
        print(f"uploaded → gs://{BUCKET}/{meta_blob}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
