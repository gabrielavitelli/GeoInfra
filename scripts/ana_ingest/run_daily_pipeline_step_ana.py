# Fragmento de referência: substituir step_ana em gcp/run_daily_pipeline.py
def step_ana(dataset: str) -> bool:
    print("\n--- 6. ANA (telemetria adotada) -> BQ ---")
    ana_dl = EFC_ROOT / "downloads" / "ana_hidroinfo_efc.py"
    downloaded = False
    if ana_dl.is_file():
        days_back = os.environ.get("ANA_HIDROINFO_DAYS_BACK", "15")
        ok_dl, err_dl = _run_retries(
            [sys.executable, "downloads/ana_hidroinfo_efc.py", "--days-back", str(days_back), "--skip-detailed"],
            EFC_ROOT,
            TIMEOUT_BNDMET,
            "ANA download",
        )
        if not ok_dl:
            # Não recarregar CSV obsoleto após HidroInfo 503/timeout — isso
            # congelava nivel_m no BQ (ex.: max_date preso em 2026-10-13).
            print(f"[FALHA] ANA download: {err_dl[:800]}")
            return False
        downloaded = True
    else:
        print("[AVISO] downloads/ana_hidroinfo_efc.py ausente; usando CSVs em data/.")
    est = DATA_DIR / "ana_estacoes_efc.csv"
    dad = DATA_DIR / "ana_telemetrica_adotada_efc.csv"
    if not est.exists() or not dad.exists():
        print("[FALHA] CSVs ANA ausentes em data/.")
        return False
    if downloaded and dad.stat().st_size < 32:
        print("[FALHA] CSV ANA adotada vazio após download.")
        return False
    cmd_bq = [sys.executable, "gcp/bq_load_clima.py", "--dataset", dataset, "--ana"]
    ok_bq, err_bq = _run_retries(cmd_bq, EFC_ROOT, TIMEOUT_BQ_LOAD, "ANA BQ load")
    if not ok_bq:
        print(f"[FALHA] ANA BQ: {err_bq[:800]}")
        return False
    print("  OK: ANA carregado no BQ.")
    return True

