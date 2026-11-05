# Verificação — figura localização sensores de nível (EFC)

**Figura analisada:** `EFC_pontos_nivel_mapa.png`  
**Título na figura:** EFC • Nível — EWS vs ANA (27 ANA no corredor · 4 EWS/pontos EFC)  
**Gerada em (resumo JSON):** 2026-10-13T19:16:20  
**Verificação em:** 2026-11-05 (America/Sao_Paulo)

## Veredito

A figura está **parcialmente correta** na geometria do corredor EFC, mas **não deve ser lida como mapa só de sensores de nível de água da EFC**:
- 2 dos 4 losangos roxos são de fato sensores/pontes de nível (Pindaré e 763+800).
- Km 85 é **abrigo pluviométrico EWS**, não sensor de nível.
- Km 661 marca o **eixo ferroviário** (Ponte Sumaúma), ~170 m do vão; não está sobre o Tocantins.

## Checagens realizadas

| Ponto | Código | Lat / Lon | No eixo EFC? | Curso d’água próximo | Papel real |
|---|---|---|---|---|---|
| Km 85 (Abrigo EWS) | EWS6987 / BS08687S | −3.298651, −44.465063 | Plausível (Entroncamento / Itapecuru-Mirim) | — (Overpass instável) | Abrigo precip, **não nível** |
| Sensor Rio Pindaré (Km 374) | **BC07541S** | −4.253738, −46.531145 | Sim (Δkm vs distância razoável) | **Rio Pindaré** (OSM ≤2 km) | Sensor de nível EFC — **OK** |
| Ponte Sumaúma (Km 661) | railway_km_661 (+ EWS6971 ~663) | −5.149214, −48.377048 | Sim (OSM: Estrada de Ferro Carajás ≤500 m) | Igarapé/vão local; Tocantins ANA ~23 km | Marco de ponte / eixo — **posição do eixo OK, interpretação hidrológica frágil** |
| Ponte EFC Km 763+800 | **BC07533S** | −5.571403, −49.241924 | Sim (EFC ≤500 m) | **Rio Vermelho** (OSM ≤2 km) | Sensor/ponte de nível — **OK** |

### Consistência km EFC × distância geodésica
- Km85 → Pindaré: 252,6 km geodésico vs 289 km-ferrovia (razão 0,87)
- Pindaré → Sumaúma: 227,5 vs 287 (0,79)
- Sumaúma → 763+800: 106,6 vs 102,8 (1,04)

Ordem espacial na figura (NE→SW) está coerente com o eixo EFC.

### ANA na figura
- 27 estações no corredor (~100 km) — coerente com a planilha `ANA_corredor_EFC`.
- Atenção: o JSON legado `EFC_pontos_nivel_resumo.json` ainda aponta ANA “mais próxima” com conjunto reduzido (ex.: Km 85 → 23800495 a **467 km**). A planilha Excel já corrige isso (Km 85 → **33690100 Itapecuru-Mirim, 15,5 km**).

### Sensores de nível Orion citados em trabalhos anteriores e **ausentes** desta figura
- BC07507S, BC07508S, EWS6984 (Water Level no catálogo) — não aparecem nos 4 losangos.

## Riscos se a figura for usada operacionalmente
1. Rotular Km 85 como “sensor de nível” induz erro.
2. Usar Km 661 como proxy de cota do Tocantins é inadequado (ANA 23800495 fica a ~23 km; ponte local é sobre curso menor).
3. XLSX locais dos sensores (`BC07541S…`, `BC07533S…`, `km85…`) não estavam montados na geração (`xlsx_incorporados: false`).

## Recomendações
1. Legenda: separar **sensor de nível EFC** vs **abrigo EWS / marco de ponte**.
2. Em Km 661, plotar **eixo** e **vão** (já existe `Ponte_Sumauma_eixo_vs_vao.png`).
3. Incluir BC07507S / BC07508S / EWS6984 se forem nível operacional.
4. Publicar nearest-ANA a partir do corredor de 27 (Excel), não do JSON de 4 estações.
