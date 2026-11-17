"""Internacionalização PT/EN sem dependência do Streamlit."""
from __future__ import annotations

from monitor_i18n import NAV_DEF, STRINGS


def get_lang(request) -> str:
    """Interface fixa em português (toggle EN desativado por enquanto)."""
    return "pt"


def t(request, key: str) -> str:
    lang = get_lang(request)
    return STRINGS.get(lang, STRINGS["pt"]).get(key, STRINGS["pt"].get(key, key))


def nav_items(request):
    lang = get_lang(request)
    d = STRINGS.get(lang, STRINGS["pt"])
    return [
        {
            "emoji": ic,
            "label": d.get(k_nav, k_nav),
            "page_key": page_key,
            "url_name": _page_url_name(page_key),
        }
        for ic, k_nav, page_key in NAV_DEF
    ]


def _page_url_name(page_key: str) -> str:
    return {
        "Visão Detalhada": "home",
        "Sensores": "sensores",
        "Previsão": "previsao",
        "Boletins": "boletins",
        "Raijin": "consultar",
        "Sobre": "sobre",
    }.get(page_key, "home")
