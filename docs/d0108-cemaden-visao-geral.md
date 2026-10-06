# D0108 CEMADEN — Visão Geral

## Metadados oficiais (INMET Mapas / parceiro CEMADEN)

Fonte: `https://apimapas.inmet.gov.br/estacoes` (entidade **CEMADEN**, código INMET `D####`).

| Campo | Valor |
|-------|--------|
| Código INMET | **D0108** |
| Nome | PARAUAPEBAS - NOVA VIDA |
| Município | Parauapebas / PA |
| Lat / Lon | **-6.0914**, **-49.9045** |
| Entidade | CEMADEN |

Código nativo CEMADEN (IBGE): `150553602A` (mesmas coords).  
Espelho ANA/BNDMET no mesmo ponto: **D6182** (já no catálogo EFC).

Outras CEMADEN em Parauapebas (também inseridas): D0107 Palmares Sul, D0109 Novo Brasil, D0110 Betânia, D0111 Centro.

## Por que o agente anterior não achou

BNDMET `tipo=todas` e API CEMADEN `311_24.json` **não** usam o código `D0108`. O `D####` é o alias do **mapa INMET** para estações parceiras CEMADEN.

## O que foi feito

1. `locations`: `estacao_D0108` (+ D0107/D0109/D0110/D0111), `source=estacao`.
2. `clima_series` (`estacoes`): precip ~120 d copiada de `estacao_D6182` (mesmo sítio físico) → marcador com série; **ingestão CEMADEN dedicada ainda não existe**.
3. CSV `bndmet_estacoes_efc.csv` atualizado; overlay de nomes no catálogo BQ.
4. `MONITOR_MAP_CACHE_VER=d0108_cemaden_v1` + redeploy monitor-efc-prod (USER2–USER6 preservados).

## Precipitação

- **Agora:** série `estacoes` em `estacao_D0108` (alias operacional de D6182).
- **Para série CEMADEN “de verdade”:** ingest a partir de `resources.cemaden.gov.br` / código `150553602A` (pipeline ainda não no monitor).
