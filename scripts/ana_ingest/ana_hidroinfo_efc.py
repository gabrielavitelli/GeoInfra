"""
Download de estações e séries telemétricas ANA para a região EFC.

Fluxo:
- Lista legada `ListaEstacoesTelemetricas` para descobrir coordenadas/metadata.
- API nova `Hidroinfoana...` para séries adotadas/detalhadas via bearer token.
- Fallback automático para `DadosHidrometeorologicos` (GET legado) quando
  HidroInfo retorna 429/5xx (ex.: 503) após retries — evita ficar preso em CSV velho.

Variáveis de ambiente:
  ANA_HIDROINFO_IDENTIFICADOR / ANA_HIDROINFO_VERIFICADOR
  ANA_HIDROINFO_SENHA
  ANA_HIDROINFO_TOKEN           (opcional; bearer token já emitido)
  ANA_HIDROINFO_BASE_URL            (default: https://www.ana.gov.br/hidrowebservice)
  ANA_HIDROINFO_LEGACY_URL          (default: https://telemetriaws1.ana.gov.br/ServiceANA.asmx)
  ANA_HIDROINFO_FILTER_TYPE         (default: DATA_ULTIMA_ATUALIZACAO)
  ANA_HIDROINFO_ADOTADA_RANGE       (default: HORA_1)
  ANA_HIDROINFO_DETALHADA_RANGE     (default: HORA_1)
  ANA_HIDROINFO_DAYS_BACK           (default: 30)
  ANA_HIDROINFO_STATION_LIMIT       (opcional; limita número de estações)
  ANA_HIDROINFO_REQUEST_PAUSE_SEC   (default: 0.15)
  ANA_HIDROINFO_HTTP_RETRIES        (default: 5)
  ANA_LEGACY_FALLBACK               (default: 1; 0 desliga fallback GET legado)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date as dt_date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter, Retry

DIR = Path(__file__).resolve().parent
ROOT = DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib_efc import LAT_MAX_EFC, LAT_MIN_EFC, LON_MAX_EFC, LON_MIN_EFC

DATA_DIR = ROOT / "data"
ANA_ESTACOES_CSV = DATA_DIR / "ana_estacoes_efc.csv"
ANA_ADOTADA_CSV = DATA_DIR / "ana_telemetrica_adotada_efc.csv"
ANA_DETALHADA_CSV = DATA_DIR / "ana_telemetrica_detalhada_efc.csv"

BASE_URL = (os.environ.get("ANA_HIDROINFO_BASE_URL") or "https://www.ana.gov.br/hidrowebservice").rstrip("/")
# Preferir HTTP no GET legado: HTTPS costuma 504 sob carga; o force-load
# operacional usa http://telemetriaws1.../DadosHidrometeorologicos.
LEGACY_URL = (
    os.environ.get("ANA_HIDROINFO_LEGACY_URL") or "http://telemetriaws1.ana.gov.br/ServiceANA.asmx"
).rstrip("/")

DEFAULT_FILTER_TYPE = (os.environ.get("ANA_HIDROINFO_FILTER_TYPE") or "DATA_ULTIMA_ATUALIZACAO").strip()
DEFAULT_ADOTADA_RANGE = (os.environ.get("ANA_HIDROINFO_ADOTADA_RANGE") or "DIAS_30").strip()
DEFAULT_DETALHADA_RANGE = (os.environ.get("ANA_HIDROINFO_DETALHADA_RANGE") or "DIAS_30").strip()

SESSION = requests.Session()
# Não incluir 503/502 no Retry do urllib3: ele levanta
# "too many 503 error responses" antes do nosso backoff/fallback legado.
SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=2,
            backoff_factor=0.8,
            status_forcelist=[429],
            allowed_methods={"GET"},
            raise_on_status=False,
        )
    ),
)
SESSION.mount(
    "http://",
    HTTPAdapter(
        max_retries=Retry(
            total=2,
            backoff_factor=0.8,
            status_forcelist=[429],
            allowed_methods={"GET"},
            raise_on_status=False,
        )
    ),
)

_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _normalize_text(value: Any) -> str:
    txt = str(value or "").strip()
    if not txt:
        return ""
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return txt


def _slug(value: Any) -> str:
    txt = _normalize_text(value).lower()
    out = []
    prev_sep = False
    for ch in txt:
        if ch.isalnum():
            out.append(ch)
            prev_sep = False
            continue
        if not prev_sep:
            out.append("_")
            prev_sep = True
    return "".join(out).strip("_")


def _read_int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _pause() -> None:
    time.sleep(float(os.environ.get("ANA_HIDROINFO_REQUEST_PAUSE_SEC", "0.15")))


def _request_with_backoff(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> requests.Response:
    attempts = _read_int_env("ANA_HIDROINFO_HTTP_RETRIES", 5, 1, 12)
    last_exc: Exception | None = None
    for att in range(attempts):
        try:
            resp = SESSION.request(method.upper(), url, params=params, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                body = (resp.text or "").strip().replace("\n", " ")[:220]
                raise requests.exceptions.HTTPError(
                    f"HTTP {resp.status_code} em {url} params={params or {}} body={body}"
                )
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_exc = exc
            if att >= attempts - 1:
                break
            wait_s = min(60.0, 2.0**att)
            print(f"[ANA] retry {att + 1}/{attempts - 1} após erro em {url}: {exc}. Aguardando {wait_s:.1f}s...")
            time.sleep(wait_s)
    assert last_exc is not None
    raise last_exc


def _resolve_identificador() -> str:
    for key in ("ANA_HIDROINFO_IDENTIFICADOR", "ANA_HIDROINFO_VERIFICADOR", "ANA_API_IDENTIFICADOR", "ANA_API_VERIFICADOR"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    try:
        from gcp.gcp_secrets import resolve_ana_hidroinfo_identificador
    except ImportError:
        try:
            from gcp_secrets import resolve_ana_hidroinfo_identificador
        except ImportError:
            resolve_ana_hidroinfo_identificador = None
    if resolve_ana_hidroinfo_identificador is not None:
        val = (resolve_ana_hidroinfo_identificador() or "").strip()
        if val:
            return val
    return ""


def _resolve_senha() -> str:
    for key in ("ANA_HIDROINFO_SENHA", "ANA_API_SENHA"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    try:
        from gcp.gcp_secrets import resolve_ana_hidroinfo_senha
    except ImportError:
        try:
            from gcp_secrets import resolve_ana_hidroinfo_senha
        except ImportError:
            resolve_ana_hidroinfo_senha = None
    if resolve_ana_hidroinfo_senha is not None:
        val = (resolve_ana_hidroinfo_senha() or "").strip()
        if val:
            return val
    return ""


def _resolve_direct_token() -> str:
    for key in ("ANA_HIDROINFO_TOKEN", "ANA_API_TOKEN", "ANA_TOKEN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    try:
        from gcp.gcp_secrets import resolve_ana_hidroinfo_token
    except ImportError:
        try:
            from gcp_secrets import resolve_ana_hidroinfo_token
        except ImportError:
            resolve_ana_hidroinfo_token = None
    if resolve_ana_hidroinfo_token is not None:
        val = (resolve_ana_hidroinfo_token() or "").strip()
        if val:
            return val
    return ""


def ana_auth_token(force_refresh: bool = False) -> str:
    now = time.time()
    cached = str(_TOKEN_CACHE.get("token") or "").strip()
    expires_at = float(_TOKEN_CACHE.get("expires_at") or 0.0)
    if not force_refresh and cached and now < (expires_at - 60.0):
        return cached

    direct_token = _resolve_direct_token()
    if direct_token:
        _TOKEN_CACHE["token"] = direct_token
        _TOKEN_CACHE["expires_at"] = now + 20 * 60
        return direct_token

    identificador = _resolve_identificador()
    senha = _resolve_senha()
    if not identificador or not senha:
        raise RuntimeError(
            "Credenciais ANA ausentes. Defina ANA_HIDROINFO_IDENTIFICADOR/ANA_HIDROINFO_VERIFICADOR e ANA_HIDROINFO_SENHA, "
            "ou forneça ANA_HIDROINFO_TOKEN. Em produção, prefira Google Secret Manager."
        )

    resp = _request_with_backoff(
        "GET",
        f"{BASE_URL}/EstacoesTelemetricas/OAUth/v1",
        headers={
            "Accept": "application/json",
            "Identificador": identificador,
            "Senha": senha,
        },
        timeout=60,
    )
    payload = resp.json()
    token = ""
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, dict):
        for key in ("tokenautenticacao", "access_token", "accessToken", "token"):
            token = str(items.get(key) or "").strip()
            if token:
                break
    if not token and isinstance(payload, dict):
        for key in ("tokenautenticacao", "access_token", "accessToken", "token"):
            token = str(payload.get(key) or "").strip()
            if token:
                break
    if not token:
        raise RuntimeError(f"Resposta de autenticação ANA sem token reconhecível: {payload}")
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = now + 55 * 60
    return token


def _auth_headers(force_refresh: bool = False) -> dict[str, str]:
    token = ana_auth_token(force_refresh=force_refresh)
    return {"Accept": "application/json", "Authorization": f"Bearer {token}"}


def _request_json_authorized(url: str, *, params: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = _request_with_backoff("GET", url, params=params, headers=_auth_headers(), timeout=90)
    except requests.exceptions.HTTPError as exc:
        if "401" not in str(exc):
            raise
        resp = _request_with_backoff("GET", url, params=params, headers=_auth_headers(force_refresh=True), timeout=90)
    payload = resp.json()
    if isinstance(payload, dict) and str(payload.get("status") or "").upper().startswith("401"):
        raise RuntimeError(f"ANA não autorizou a chamada: {payload}")
    return payload if isinstance(payload, dict) else {"items": payload}


def fetch_hidroinfoana_serie(
    endpoint_name: str,
    cod_estacao: str | int,
    *,
    data_busca: dt_date,
    tipo_filtro_data: str = DEFAULT_FILTER_TYPE,
    range_intervalo: str = DEFAULT_ADOTADA_RANGE,
) -> dict[str, Any]:
    url = f"{BASE_URL}/EstacoesTelemetricas/{endpoint_name}/v1"
    params = {
        "Código da Estação": int(str(cod_estacao).strip()),
        "Tipo Filtro Data": str(tipo_filtro_data).strip(),
        "Data de Busca (yyyy-MM-dd)": pd.Timestamp(data_busca).strftime("%Y-%m-%dT00:00:00"),
        "Range Intervalo de busca": str(range_intervalo).strip(),
    }
    return _request_json_authorized(url, params=params)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def fetch_legacy_station_catalog(status_estacoes: str = "0", origem: str = "") -> pd.DataFrame:
    resp = _request_with_backoff(
        "GET",
        f"{LEGACY_URL}/ListaEstacoesTelemetricas",
        params={"statusEstacoes": status_estacoes, "origem": origem},
        headers={"Accept": "text/xml"},
        timeout=120,
    )
    root = ET.fromstring(resp.content)
    rows: list[dict[str, Any]] = []
    for table in root.iter():
        if _local_name(table.tag) != "Table":
            continue
        row: dict[str, Any] = {}
        for child in list(table):
            row[_local_name(child.tag)] = (child.text or "").strip()
        if row:
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["CodEstacao", "NomeEstacao", "Latitude", "Longitude"])
    out = pd.DataFrame(rows)
    for col in ("Latitude", "Longitude", "Altitude", "SubBacia", "CodRio"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def filter_station_catalog_bbox(
    df: pd.DataFrame,
    *,
    lat_min: float = LAT_MIN_EFC,
    lat_max: float = LAT_MAX_EFC,
    lon_min: float = LON_MIN_EFC,
    lon_max: float = LON_MAX_EFC,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["codEstacao", "nome", "latitude", "longitude"])
    out = df.copy()
    if "Latitude" not in out.columns or "Longitude" not in out.columns:
        return pd.DataFrame(columns=["codEstacao", "nome", "latitude", "longitude"])
    out = out[
        out["Latitude"].between(float(lat_min), float(lat_max), inclusive="both")
        & out["Longitude"].between(float(lon_min), float(lon_max), inclusive="both")
    ].copy()
    if out.empty:
        return pd.DataFrame(columns=["codEstacao", "nome", "latitude", "longitude"])
    out = out.rename(
        columns={
            "CodEstacao": "codEstacao",
            "NomeEstacao": "nome",
            "Latitude": "latitude",
            "Longitude": "longitude",
            "Municipio-UF": "municipio_uf",
            "StatusEstacao": "statusEstacao",
            "Origem": "origem",
            "Operadora": "operadora",
            "Responsavel": "responsavel",
            "NomeRio": "nomeRio",
            "CodRio": "codRio",
        }
    )
    keep = [
        c
        for c in [
            "codEstacao",
            "nome",
            "latitude",
            "longitude",
            "municipio_uf",
            "statusEstacao",
            "origem",
            "operadora",
            "responsavel",
            "nomeRio",
            "codRio",
            "Bacia",
            "SubBacia",
            "Altitude",
        ]
        if c in out.columns
    ]
    out["codEstacao"] = out["codEstacao"].astype(str).str.strip()
    out = out[keep].drop_duplicates(subset=["codEstacao"], keep="first").reset_index(drop=True)
    return out


def _extract_items(payload: Any) -> Any:
    if isinstance(payload, dict):
        items = payload.get("items")
        if items is not None:
            return items
    return payload


def _parse_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    txt = str(value).strip()
    if not txt:
        return None
    try:
        ts = pd.to_datetime(txt, errors="coerce", utc=False)
    except Exception:
        return None
    if ts is None or pd.isna(ts):
        return None
    if getattr(ts, "tzinfo", None) is not None:
        try:
            ts = ts.tz_convert(None)
        except Exception:
            try:
                ts = ts.tz_localize(None)
            except Exception:
                pass
    return pd.Timestamp(ts)


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", ".").strip()
        num = float(value)
        if pd.isna(num):
            return None
        return float(num)
    except (TypeError, ValueError):
        return None


def _extract_measurement_records(node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            out.extend(_extract_measurement_records(item))
        return out
    if not isinstance(node, dict):
        return out
    ts_key = None
    for key in node.keys():
        sk = _slug(key)
        if any(tok in sk for tok in ("data_hora", "data_leitura", "data_medicao", "data_ultima_atualizacao", "data")):
            if _parse_timestamp(node.get(key)) is not None:
                ts_key = key
                break
    if ts_key is not None:
        out.append(node)
        return out
    for value in node.values():
        if isinstance(value, (list, dict)):
            out.extend(_extract_measurement_records(value))
    return out


def _map_variable_name(raw_key: str, *, default_prefix: str) -> tuple[str | None, str]:
    key = _slug(raw_key)
    if not key:
        return None, "desconhecido"
    # Evita mapear Cota_Adotada_Status / Vazao_*_Status como série física.
    if key.endswith("_status") or key.endswith("status"):
        return None, "desconhecido"
    if "adot" in key:
        origem = "adotado"
    elif "brut" in key:
        origem = "bruto"
    else:
        origem = default_prefix
    if "chuva" in key or "precip" in key:
        return ("precip_mm" if origem == "adotado" else f"precip_mm_{origem}"), origem
    if "vazao" in key:
        return ("vazao_m3s" if origem == "adotado" else f"vazao_m3s_{origem}"), origem
    if "nivel" in key or "cota" in key:
        return ("nivel_m" if origem == "adotado" else f"nivel_m_{origem}"), origem
    return None, origem


def _scale_ana_value(var_name: str, value: float) -> float:
    """Cota/Nível da telemétrica ANA vêm em cm → nivel_m."""
    if var_name.startswith("nivel_m") and abs(value) >= 20.0:
        return float(value) / 100.0
    return float(value)


def _response_to_series_rows(
    payload: dict[str, Any],
    cod_estacao: str,
    *,
    endpoint_label: str,
) -> list[dict[str, Any]]:
    items = _extract_items(payload)
    records = _extract_measurement_records(items)
    rows: list[dict[str, Any]] = []
    for rec in records:
        ts = None
        for key in rec.keys():
            ts = _parse_timestamp(rec.get(key))
            if ts is not None:
                break
        if ts is None:
            continue
        for raw_key, raw_value in rec.items():
            val = _coerce_float(raw_value)
            if val is None:
                continue
            var_name, origem = _map_variable_name(raw_key, default_prefix="adotado" if endpoint_label == "adotada" else "detalhada")
            if var_name is None:
                continue
            rows.append(
                {
                    "dataHora": ts.isoformat(sep=" "),
                    "codEstacao": str(cod_estacao).strip(),
                    "variavel": var_name,
                    "valor": _scale_ana_value(var_name, float(val)),
                    "origemValor": origem,
                    "fonteApi": endpoint_label,
                    "payloadChave": _slug(raw_key),
                }
            )
    return rows


def fetch_legacy_dados_hidrometeorologicos(
    cod_estacao: str | int,
    *,
    start: dt_date,
    end: dt_date,
) -> list[dict[str, Any]]:
    """GET legado telemetriaws1 DadosHidrometeorologicos (fallback p/ HidroInfo 503)."""
    params = {
        "codEstacao": str(cod_estacao).strip(),
        "dataInicio": pd.Timestamp(start).strftime("%d/%m/%Y"),
        "dataFim": pd.Timestamp(end).strftime("%d/%m/%Y"),
    }
    resp = _request_with_backoff(
        "GET",
        f"{LEGACY_URL}/DadosHidrometeorologicos",
        params=params,
        headers={"Accept": "text/xml"},
        timeout=120,
    )
    root = ET.fromstring(resp.content)
    rows: list[dict[str, Any]] = []
    for el in root.iter():
        if _local_name(el.tag) != "DadosHidrometereologicos":
            continue
        row = {_local_name(c.tag): (c.text or "").strip() for c in list(el)}
        if row:
            rows.append(row)
    return rows


def _legacy_rows_to_series(
    raw_rows: list[dict[str, Any]],
    cod_estacao: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in raw_rows:
        ts = _parse_timestamp(rec.get("DataHora"))
        if ts is None:
            continue
        nivel = _coerce_float(rec.get("Nivel"))
        if nivel is not None:
            out.append(
                {
                    "dataHora": ts.isoformat(sep=" "),
                    "codEstacao": str(cod_estacao).strip(),
                    "variavel": "nivel_m",
                    "valor": _scale_ana_value("nivel_m", float(nivel)),
                    "origemValor": "adotado",
                    "fonteApi": "legacy_dados",
                    "payloadChave": "nivel",
                }
            )
        vazao = _coerce_float(rec.get("Vazao"))
        if vazao is not None:
            out.append(
                {
                    "dataHora": ts.isoformat(sep=" "),
                    "codEstacao": str(cod_estacao).strip(),
                    "variavel": "vazao_m3s",
                    "valor": float(vazao),
                    "origemValor": "adotado",
                    "fonteApi": "legacy_dados",
                    "payloadChave": "vazao",
                }
            )
        chuva = _coerce_float(rec.get("Chuva"))
        if chuva is not None:
            out.append(
                {
                    "dataHora": ts.isoformat(sep=" "),
                    "codEstacao": str(cod_estacao).strip(),
                    "variavel": "precip_mm",
                    "valor": float(chuva),
                    "origemValor": "adotado",
                    "fonteApi": "legacy_dados",
                    "payloadChave": "chuva",
                }
            )
    return out


def _legacy_fallback_enabled() -> bool:
    return (os.environ.get("ANA_LEGACY_FALLBACK") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _dedupe_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["dataHora", "codEstacao", "variavel", "valor", "origemValor", "fonteApi", "payloadChave"])
    out = df.copy()
    out["dataHora"] = pd.to_datetime(out["dataHora"], errors="coerce")
    out = out.dropna(subset=["dataHora", "codEstacao", "variavel", "valor"])
    out["codEstacao"] = out["codEstacao"].astype(str).str.strip()
    out["variavel"] = out["variavel"].astype(str).str.strip()
    out["valor"] = pd.to_numeric(out["valor"], errors="coerce")
    out = out.dropna(subset=["valor"])
    out = out.sort_values(["codEstacao", "variavel", "dataHora", "origemValor", "fonteApi"])
    out = out.drop_duplicates(subset=["codEstacao", "variavel", "dataHora", "origemValor"], keep="last")
    out["dataHora"] = out["dataHora"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out.reset_index(drop=True)


def _iter_window_end_dates(end_date: dt_date, days_back: int, chunk_days: int = 30) -> list[dt_date]:
    total_days = max(1, int(days_back))
    chunk = max(1, min(30, int(chunk_days)))
    out: list[dt_date] = []
    cur = pd.Timestamp(end_date).date()
    remaining = total_days
    while remaining > 0:
        out.append(cur)
        cur = (pd.Timestamp(cur) - pd.Timedelta(days=chunk)).date()
        remaining -= chunk
    return out


def download_ana_series_for_stations(
    station_codes: list[str],
    *,
    end_date: dt_date,
    days_back: int,
    filter_type: str = DEFAULT_FILTER_TYPE,
    adopted_range: str = DEFAULT_ADOTADA_RANGE,
    detailed_range: str = DEFAULT_DETALHADA_RANGE,
    fetch_detailed: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    adopted_rows: list[dict[str, Any]] = []
    detailed_rows: list[dict[str, Any]] = []
    window_end_dates = _iter_window_end_dates(end_date, days_back, chunk_days=30)
    legacy_ok = _legacy_fallback_enabled()
    for idx, cod in enumerate(station_codes, start=1):
        print(f"[ANA] estação {idx}/{len(station_codes)}: {cod}")
        station_got_hidroinfo = False
        for win_date in window_end_dates:
            try:
                payload_ad = fetch_hidroinfoana_serie(
                    "HidroinfoanaSerieTelemetricaAdotada",
                    cod,
                    data_busca=win_date,
                    tipo_filtro_data=filter_type,
                    range_intervalo=adopted_range,
                )
                adopted_rows.extend(
                    _response_to_series_rows(payload_ad, str(cod), endpoint_label="adotada")
                )
                station_got_hidroinfo = True
                _pause()
                if fetch_detailed:
                    payload_det = fetch_hidroinfoana_serie(
                        "HidroinfoanaSerieTelemetricaDetalhada",
                        cod,
                        data_busca=win_date,
                        tipo_filtro_data=filter_type,
                        range_intervalo=detailed_range,
                    )
                    detailed_rows.extend(
                        _response_to_series_rows(payload_det, str(cod), endpoint_label="detalhada")
                    )
                    _pause()
            except Exception as exc:
                print(f"[ANA] HidroInfo falhou em {cod}@{win_date}: {exc}")
                if not legacy_ok:
                    print(f"[ANA] ANA_LEGACY_FALLBACK desligado; pulando {cod}@{win_date}")
                    continue
                # Janela menor no legado evita 504 em ranges longos.
                start = (pd.Timestamp(win_date) - pd.Timedelta(days=min(14, days_back) - 1)).date()
                try:
                    legacy_raw = fetch_legacy_dados_hidrometeorologicos(
                        cod, start=start, end=win_date
                    )
                    legacy_series = _legacy_rows_to_series(legacy_raw, str(cod))
                    print(
                        f"[ANA] fallback legado {cod} {start}..{win_date}: "
                        f"raw={len(legacy_raw)} series={len(legacy_series)}"
                    )
                    adopted_rows.extend(legacy_series)
                    _pause()
                except Exception as leg_exc:
                    # Não abortar o download inteiro por 1 estação — BNDMET/restante
                    # das estações devem seguir; step_ana falha só se CSV ficar vazio.
                    print(f"[ANA] fallback legado também falhou em {cod}: {leg_exc} (continua)")
                    continue
    if not adopted_rows and not detailed_rows:
        raise RuntimeError(
            "ANA: nenhuma série obtida (HidroInfo e legado falharam em todas as estações)."
        )
    return _dedupe_rows(pd.DataFrame(adopted_rows)), _dedupe_rows(pd.DataFrame(detailed_rows))


def save_dataframe(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (df if df is not None else pd.DataFrame()).to_csv(path, index=False, encoding="utf-8")


def main(
    output_estacoes: Path | str | None = None,
    output_adotada: Path | str | None = None,
    output_detalhada: Path | str | None = None,
    *,
    stations_only: bool = False,
    days_back: int | None = None,
    fetch_detailed: bool = True,
    station_limit: int | None = None,
) -> int:
    out_est = Path(output_estacoes) if output_estacoes else ANA_ESTACOES_CSV
    out_ad = Path(output_adotada) if output_adotada else ANA_ADOTADA_CSV
    out_det = Path(output_detalhada) if output_detalhada else ANA_DETALHADA_CSV

    print(f"[ANA] catálogo legado: {LEGACY_URL}")
    est_full = fetch_legacy_station_catalog(status_estacoes="0", origem="")
    est_bbox = filter_station_catalog_bbox(est_full)
    save_dataframe(est_bbox, out_est)
    print(f"[ANA] estações no bbox EFC: {len(est_bbox)} -> {out_est}")

    if stations_only:
        return 0

    station_codes = est_bbox["codEstacao"].astype(str).tolist() if not est_bbox.empty else []
    if station_limit is not None:
        station_codes = station_codes[: max(0, int(station_limit))]
    env_limit = (os.environ.get("ANA_HIDROINFO_STATION_LIMIT") or "").strip()
    if env_limit:
        station_codes = station_codes[: max(0, int(env_limit))]
    if not station_codes:
        print("[ANA] sem estações no bbox para baixar séries.")
        save_dataframe(pd.DataFrame(), out_ad)
        save_dataframe(pd.DataFrame(), out_det)
        return 0

    days = max(1, int(days_back or os.environ.get("ANA_HIDROINFO_DAYS_BACK", "30")))
    end_date = datetime.now(timezone.utc).date()
    adopted_df, detailed_df = download_ana_series_for_stations(
        station_codes,
        end_date=end_date,
        days_back=days,
        fetch_detailed=fetch_detailed,
    )
    save_dataframe(adopted_df, out_ad)
    print(f"[ANA] séries adotadas: {len(adopted_df)} linhas -> {out_ad}")
    if fetch_detailed:
        save_dataframe(detailed_df, out_det)
        print(f"[ANA] séries detalhadas: {len(detailed_df)} linhas -> {out_det}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Baixa catálogo e séries ANA/Hidroinfo para a EFC.")
    ap.add_argument("--stations-only", action="store_true", help="Somente catálogo de estações no bbox EFC.")
    ap.add_argument("--days-back", type=int, default=None, help="Janela em dias para séries (máx. 30 dias por requisição).")
    ap.add_argument("--skip-detailed", action="store_true", help="Não baixar a série detalhada.")
    ap.add_argument("--station-limit", type=int, default=None, help="Limitar número de estações (debug).")
    ap.add_argument("--output-estacoes", default=None, help="CSV de catálogo ANA.")
    ap.add_argument("--output-adotada", default=None, help="CSV da série adotada ANA.")
    ap.add_argument("--output-detalhada", default=None, help="CSV da série detalhada ANA.")
    args = ap.parse_args()
    raise SystemExit(
        main(
            output_estacoes=args.output_estacoes,
            output_adotada=args.output_adotada,
            output_detalhada=args.output_detalhada,
            stations_only=args.stations_only,
            days_back=args.days_back,
            fetch_detailed=not args.skip_detailed,
            station_limit=args.station_limit,
        )
    )
