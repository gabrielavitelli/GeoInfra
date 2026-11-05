# ANA HidroInfo 503 — patch de ingestão

## Causa
Cloud Run `efc-ingestion-stations` (MODEL=daily-stations) falhava o step `ana` com:

`urllib3.exceptions.ResponseError: too many 503 error responses`

na API `https://www.ana.gov.br/hidrowebservice/EstacoesTelemetricas/...`.
O pipeline aceitava partial e **recarregava CSV antigo** → `clima_series` (`source=ana`, `variable=nivel_m`) ficou em **2026-10-13**.

## URLs
| Uso | URL |
|-----|-----|
| HidroInfo OAuth | `https://www.ana.gov.br/hidrowebservice/EstacoesTelemetricas/OAUth/v1` |
| Série adotada | `.../HidroinfoanaSerieTelemetricaAdotada/v1` |
| Fallback legado | `http://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos` |
| Catálogo legado | `.../ListaEstacoesTelemetricas` |

## Catch-up imediato
```bash
python scripts/force_ana_nivel_bq_load.py --days 30
```

## Aplicar no código do monitor (imagem `efc-ingestion`)
1. Substituir `downloads/ana_hidroinfo_efc.py` pelo arquivo deste diretório (ou aplicar `ana_hidroinfo_efc.py.patch`).
2. Em `gcp/run_daily_pipeline.py` `step_ana`: **não** carregar CSV após falha de download (ver comentário HidroInfo 503 no patch aplicado em produção via rebuild).
3. Rebuild/redeploy só da imagem/job de ingestão (BNDMET inalterado).

Env opcional: `ANA_LEGACY_FALLBACK=1` (default), `ANA_HIDROINFO_HTTP_RETRIES=5`.
