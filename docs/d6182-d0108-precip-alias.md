# D6182 ↔ D0108 — alias e verdade de precipitação

## Metadados (confirmado)

| Rede | Código | Nome | Lat / Lon |
|------|--------|------|-----------|
| BNDMET / ANA espelho | **D6182** | PARAUAPEBAS | -6.0914, -49.9044 |
| INMET mapas (entidade CEMADEN) | **D0108** | PARAUAPEBAS - NOVA VIDA | -6.0914, -49.9045 |
| CEMADEN nativo | **150553602A** (idestacao 6798) | Nova Vida | mesmas coords |

São o **mesmo sítio físico**. O código `D0108` não existe na API BNDMET `tipo=todas`; é o alias do **mapa INMET** para a PCD CEMADEN.

## Comparativo 30 dias (2026-09-07 → 2026-10-06)

| Fonte | Acumulado ~30 d | Notas |
|-------|-----------------|-------|
| INMET (relato usuário) | **~30 mm** | Provável janela curta / evento recente (ver 120 h) |
| Open-Meteo no ponto | ~28 mm | Grade, não ponto medido |
| BNDMET API I175 `D6182` | **9 mm** | Só 2 horas ≠0 (2 mm em 25/09, 7 mm em 05/10) |
| BQ antigo `estacao_D6182` (mapa MAX/slot) | **9 mm** | Fidelidade ao BNDMET; SUM bruto ~30 mm era **duplicata** |
| CEMADEN horário Nova Vida | **75,8 mm** | 25/09=27,2; 26/09=9,4; **05/10=35,6** |
| Após fix (mapa 120 h) | **36,0 mm** | Alinhado ao ~30 mm do INMET se a UI/mapa for janela curta |
| Após fix (mapa 30 d / 720 h) | **75,8 mm** | Verdade CEMADEN no ponto |

### Causa raiz

1. Catálogo/mapa buscavam `D0108` como código BNDMET → “não existe”.
2. Espelho BNDMET `D6182` tem série **esparsa** (subconta vs CEMADEN/INMET).
3. `clima_series` tinha milhares de linhas duplicadas no mesmo `date` (zeros + poucos valores); o mapa usa `MAX` por slot → mostrava **baixo**; um `SUM` ingênuo batia ~30 mm por coincidência.

## Correção aplicada

1. **Alias canônico:** precip CEMADEN Nova Vida gravada em `estacao_D6182` (e espelho operacional `estacao_D0108`).
2. Script: `scripts/ingest_cemaden_d0108_to_d6182.py` (API `mapservices.cemaden.gov.br/.../horario/{id}/{horas}`).
3. `lib_efc.py`: `_STATION_CODE_ALIASES` D0108→D6182; dedupe do mapa por **lat/lon** preferindo marcador **D0108**.
4. Cache bust: `MONITOR_MAP_CACHE_VER=d6182_d0108_alias_marker_v3` (marcador único D0108).
5. Imagem rebuild com patch de alias/dedupe (preferir **D0108** sobre D6182 no mesmo lat/lon).

## Onde ver no mapa (Visão Geral)

1. Abrir **Visão Geral** do monitor EFC (prod).
2. Acumulado **120 h** (ou janela próxima): marcador em Parauapebas ~**36 mm** (rótulo preferencial **D0108** Nova Vida).
3. Coordenadas ≈ **−6,09, −49,90** (km EFC ~883).
4. Preservados: linha amarela Previsão, card entorno, USER2–USER6.

## Coordenação com agente D0108

O agente `bc-f43980c2` adicionou D0108 ao catálogo/locations. Este fix **não cria série inventada**: substitui a cópia rasa de D6182 (9 mm) pela série CEMADEN e mantém `estacao_D6182` como destino canônico do remap.

## Status deploy

- Live: **`monitor-efc-prod-00168-nnp`** (`MONITOR_MAP_CACHE_VER=d6182_d0108_alias_marker_v3`).
- Mapa 120 h: **1** popup `D0108` / PARAUAPEBAS - NOVA VIDA (CEMADEN), **36,0 mm**; sem popup `D6182`.
- Patch `lib_efc_d6182_d0108_alias_precip.patch` aplicado em `lib_efc.py` do source de deploy.
- Rebuild `monitor-efc-prod` com `MONITOR_MAP_CACHE_VER=d6182_d0108_alias_marker_v3`.
- Resultado esperado: **1 marcador** Parauapebas (**D0108** Nova Vida) com precip CEMADEN ~30+ mm.
- Dedupe: agrupa por alias canônico D0108↔D6182 (coords diferem 0,0001° lon) + round lat/lon a 3 casas.
