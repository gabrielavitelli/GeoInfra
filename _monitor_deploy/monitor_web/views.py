from __future__ import annotations

import logging
import os
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from monitor_web.i18n import get_lang
from monitor_web.decorators import staff_monitor_required
from monitor_web.template_paths import is_portal_request, monitor_template
from monitor_web.services.boletim_era5_climatology import build_boletim_era5_climatology_context
from monitor_web.services.boletins import build_boletim_dia_distribuicao_payload, build_boletins_context
from monitor_web.services.branding import logo_context
from monitor_web.services.home import build_home_context, build_home_shell_context
from monitor_web.services.home_accumulated_alerts import build_accumulated_alerts_payload
from monitor_web.services.home_fragments import (
    build_charts_fragment,
    build_charts_payload,
    build_indicators_fragment,
    build_map_embed_context,
    build_map_fragment,
    build_status_fragment,
)
from monitor_web.services.consultar_format import format_consultar_table_html, markdown_to_safe_html
from monitor_web.services.plotly_charts import llm_chart_from_df, sanitize_for_json
from monitor_web.services.previsao import (
    build_previsao_charts_payload,
    build_previsao_heatmap_payload,
    build_previsao_page_context,
    build_previsao_verification_payload,
)

logger = logging.getLogger(__name__)


from monitor_web.auth_service import (
    authenticate_monitor,
    get_profile,
    login_monitor,
    logout_monitor,
)
from monitor_web.models import Audience


def _credentials() -> tuple[str, str]:
    import os

    return (
        (os.environ.get("MONITOR_LOGIN_USER") or "adm").strip(),
        (os.environ.get("MONITOR_LOGIN_PASSWORD") or "adm").strip(),
    )


@ensure_csrf_cookie
@require_GET
def login_view(request):
    if getattr(settings, "MONITOR_AUTH_DISABLED", False):
        return redirect("monitor:home")
    if getattr(request, "user", None) and request.user.is_authenticated:
        from monitor_web.auth_service import is_internal_operator

        if is_internal_operator(request.user):
            return redirect("monitor:home")
    logo = logo_context()
    return render(
        request,
        "monitor/login.html",
        {
            "error": None,
            "show_auth_dev_hint": getattr(settings, "MONITOR_AUTH_DISABLED", False),
            **logo,
        },
    )


@require_POST
def login_submit(request):
    if getattr(settings, "MONITOR_AUTH_DISABLED", False):
        request.session["efc_authenticated"] = True
        return redirect(request.GET.get("next") or "monitor:home")
    u = (request.POST.get("username") or "").strip()
    p = request.POST.get("password") or ""
    result = authenticate_monitor(u, p, expected_audience=Audience.INTERNAL)
    if not result.ok or result.user is None:
        logo = logo_context()
        return render(
            request,
            "monitor/login.html",
            {
                "error": result.error or "Usuário ou senha inválidos.",
                "show_auth_dev_hint": getattr(settings, "MONITOR_AUTH_DISABLED", False),
                **logo,
            },
        )
    login_monitor(request, result.user)
    nxt = request.POST.get("next") or request.GET.get("next") or "/"
    return redirect(nxt)


@require_POST
def logout_view(request):
    logout_monitor(request)
    return redirect("monitor:login")


@require_GET
def set_lang(request):
    lang = request.GET.get("lang", "pt")
    request.session["ui_lang"] = "en" if lang.startswith("en") else "pt"
    ref = request.META.get("HTTP_REFERER") or "/"
    return redirect(ref)


@require_GET
def home_view(request):
    lang = get_lang(request)
    page = {"page_title": "Visão Detalhada" if lang == "pt" else "Overview"}
    if (request.GET.get("full") or "").strip() in ("1", "true", "yes"):
        ctx = build_home_context(request)
        ctx.update(page)
        return render(request, "monitor/home.html", ctx)
    ctx = build_home_shell_context(request)
    ctx.update(page)
    return render(request, monitor_template(request, "home_shell.html"), ctx)


@require_GET
def home_fragment_bootstrap(request):
    """Portal: um request HTMX preenche hero + alertas + excedências (OOB)."""
    from monitor_web.services.home_accumulated_alerts import indicators_period_query_string
    from monitor_web.services.home_fragments import build_status_fragment_portal_fast

    force = (request.GET.get("force") or "").strip().lower() in ("1", "true", "yes")
    if force:
        ctx = build_status_fragment(request)
    else:
        ctx = build_status_fragment_portal_fast(request)
    ctx["indicators_period_qs"] = indicators_period_query_string(request)
    return render(request, monitor_template(request, "fragments/home_bootstrap.html"), ctx)


@require_GET
def home_fragment_status(request):
    ctx = build_status_fragment(request)
    name = "fragments/home_status_hero.html" if is_portal_request(request) else "fragments/home_status.html"
    return render(request, monitor_template(request, name), ctx)


@require_GET
def home_fragment_alerts(request):
    return render(
        request,
        monitor_template(request, "fragments/home_alerts.html"),
        build_status_fragment(request),
    )


@require_GET
def home_fragment_exceed(request):
    return render(
        request,
        monitor_template(request, "fragments/home_exceed.html"),
        build_status_fragment(request),
    )


@require_GET
def home_fragment_indicators(request):
    from monitor_web.services.home_fragments import (
        build_indicators_fragment,
        build_indicators_fragment_portal_fast,
    )

    force = (request.GET.get("force") or "").strip().lower() in ("1", "true", "yes")
    if is_portal_request(request) and not force:
        ctx = build_indicators_fragment_portal_fast(request)
    else:
        # Staff: cache rápido; com ?force=1 carrega BQ com timeout.
        ctx = build_indicators_fragment(request)
    if not ctx.get("indicators_ctx", {}).get("available"):
        ctx.setdefault("indicators_ctx", {"available": False})
    return render(request, monitor_template(request, "fragments/home_indicators.html"), ctx)


@require_GET
def home_fragment_map(request):
    return render(
        request,
        monitor_template(request, "fragments/home_map.html"),
        build_map_fragment(request),
    )


@require_GET
@xframe_options_sameorigin
def home_map_embed(request):
    return render(request, "monitor/home_map_embed.html", build_map_embed_context(request))


@require_GET
def home_fragment_charts(request):
    return render(request, "monitor/fragments/home_charts.html", build_charts_fragment(request))


@require_GET
def home_charts_api(request):
    """Gráficos detalhados como JSON (Plotly) — evita <script> em swap HTMX."""
    try:
        payload = build_charts_payload(request)
        return JsonResponse(sanitize_for_json(payload))
    except Exception as exc:
        logger.exception("home_charts_api")
        return JsonResponse({"available": False, "error": str(exc), "charts": {}}, status=500)


@require_GET
def previsao_view(request):
    from monitor_i18n import t

    ctx = build_previsao_page_context(request)
    ctx["page_title"] = t("nav_forecast")
    return render(request, monitor_template(request, "previsao.html"), ctx)


@require_GET
def previsao_heatmap_api(request):
    """Heatmap da página Previsão (carrega ao abrir a página)."""
    try:
        payload = build_previsao_heatmap_payload(request)
        return JsonResponse(sanitize_for_json(payload))
    except Exception as exc:
        logger.exception("previsao_heatmap_api")
        return JsonResponse({"available": False, "error": str(exc), "charts": {}}, status=500)


@require_GET
def previsao_charts_api(request):
    """Km em alerta e comparativo diário (sob demanda, botão Carregar)."""
    try:
        payload = build_previsao_charts_payload(request)
        return JsonResponse(sanitize_for_json(payload))
    except Exception as exc:
        logger.exception("previsao_charts_api")
        return JsonResponse({"available": False, "error": str(exc), "charts": {}}, status=500)


@require_GET
def previsao_verification_api(request):
    """Métricas ROC / PBIAS / FAR / POD agregadas na ferrovia inteira."""
    try:
        payload = build_previsao_verification_payload(request)
        return JsonResponse(sanitize_for_json(payload))
    except Exception as exc:
        logger.exception("previsao_verification_api")
        return JsonResponse({"available": False, "error": str(exc), "models": []}, status=500)


@require_GET
def previsao_ativos_api(request):
    """Ativos (trechos): média na faixa km e suscetibilidade por dia de referência."""
    try:
        from monitor_web.services.previsao import build_previsao_ativos_payload

        payload = build_previsao_ativos_payload(request)
        return JsonResponse(sanitize_for_json(payload))
    except Exception as exc:
        logger.exception("previsao_ativos_api")
        return JsonResponse({"available": False, "error": str(exc), "segments": []}, status=500)


@require_GET
def boletins_view(request):
    from monitor_i18n import t

    ctx = build_boletins_context(request)
    ctx["page_title"] = t("nav_bulletins")
    return render(request, monitor_template(request, "boletins.html"), ctx)


@require_GET
@staff_monitor_required
def boletim_era5_climatology_view(request):
    ctx = build_boletim_era5_climatology_context(request)
    ctx["page_title"] = f"Climatologia ERA5 · {ctx.get('year', 2026)}"
    return render(request, "monitor/boletim_era5_climatology.html", ctx)


@require_GET
@staff_monitor_required
def boletim_era5_export_view(request):
    from django.http import FileResponse, Http404

    rel = (request.GET.get("file") or "").strip().replace("\\", "/")
    if not rel or ".." in rel:
        raise Http404
    if not rel.startswith("figures/era5_km_climatology/"):
        raise Http404
    root = Path(__file__).resolve().parent.parent
    path = (root / rel).resolve()
    export_root = (root / "figures" / "era5_km_climatology").resolve()
    if not str(path).startswith(str(export_root)) or not path.is_file():
        raise Http404
    inline = path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    return FileResponse(
        open(path, "rb"),
        as_attachment=not inline,
        filename=path.name,
    )


@require_GET
def boletim_dia_distribuicao_api(request):
    """Distribuição sub-diária MERGE para um dia (clique em excedência no boletim)."""
    try:
        payload = build_boletim_dia_distribuicao_payload(request)
        return JsonResponse(sanitize_for_json(payload))
    except Exception as exc:
        logger.exception("boletim_dia_distribuicao_api")
        return JsonResponse({"available": False, "error": str(exc)}, status=500)


@require_GET
def sobre_view(request):
    lang = get_lang(request)
    return render(
        request,
        monitor_template(request, "sobre.html"),
        {"page_title": "Sobre" if lang == "pt" else "About", "lang_pt": lang == "pt"},
    )


@require_GET
def consultar_view(request):
    from monitor_i18n import t
    from monitor_web.services.raijin import raijin_welcome_message

    lang = get_lang(request)
    lang_pt = lang != "en"
    examples = [
        "Chuva 24 h no km 379 — MERGE vs ECMWF, últimos 14 dias",
        "Top 20 km com maior pico de chuva 24 h nos últimos 30 dias",
        "Maior chuva registrada por estação meteorológica nos últimos 90 dias",
        "Quilômetros que ultrapassaram o limiar nos últimos 7 dias",
        "Maior chuva 24 h ontem no corredor EFC",
    ]
    history = list(request.session.get("llm_chat_history") or [])
    if not history:
        history = [raijin_welcome_message(lang_pt=lang_pt)]
        request.session["llm_chat_history"] = history
    return render(
        request,
        monitor_template(request, "consultar.html"),
        {
            "page_title": t("nav_query"),
            "lang_pt": lang_pt,
            "examples": examples,
            "history": history,
            "groq_missing": False,
        },
    )


_CONSULTAR_HISTORY_CAP = 12  # 6 turns — cookie signed ~4 KB


def _slim_chat_entry(entry: dict | None) -> dict:
    """Histórico mínimo na sessão (evita estourar cookie e perder auth)."""
    if not isinstance(entry, dict):
        return {"role": "assistant", "text": ""}
    role = entry.get("role") or "assistant"
    text = str(entry.get("text") or "")
    if len(text) > 1800:
        text = text[:1800].rstrip() + "…"
    out: dict = {"role": role, "text": text}
    if entry.get("welcome"):
        out["welcome"] = True
    if role == "assistant":
        chips = entry.get("chips") or []
        slim_chips = []
        for c in chips:
            if not isinstance(c, dict):
                continue
            label = (c.get("label") or "").strip()
            q = (c.get("q") or "").strip()
            if label or q:
                slim_chips.append({"label": label[:80], "q": q[:240]})
            if len(slim_chips) >= 6:
                break
        if slim_chips:
            out["chips"] = slim_chips
        href = (entry.get("previsao_url") or "").strip()
        if href:
            out["previsao_url"] = href[:200]
    return out


def _append_consultar_history(request, *entries: dict) -> None:
    """Persiste histórico slim do chat (lista mutável in-place não grava na sessão Django)."""
    history = list(request.session.get("llm_chat_history") or [])
    if entries and any(e.get("role") == "user" for e in entries):
        history = [m for m in history if not m.get("welcome")]
    for e in entries:
        history.append(_slim_chat_entry(e))
    if len(history) > _CONSULTAR_HISTORY_CAP:
        history = history[-_CONSULTAR_HISTORY_CAP:]
    request.session["llm_chat_history"] = history
    request.session.modified = True


def _history_for_client(request, *, rich_last_assistant: dict | None = None) -> list:
    history = list(request.session.get("llm_chat_history") or [])
    if rich_last_assistant and history and history[-1].get("role") == "assistant":
        return history[:-1] + [rich_last_assistant]
    return history


def _consultar_wants_json(request) -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    if "application/json" in accept:
        return True
    if (request.POST.get("format") or request.GET.get("format") or "").strip().lower() == "json":
        return True
    return (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"


def _consultar_wants_stream(request) -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    if "text/event-stream" in accept:
        return True
    flag = (request.POST.get("stream") or request.GET.get("stream") or "").strip().lower()
    return flag in ("1", "true", "yes")


def _consultar_redirect(request):
    if is_portal_request(request):
        return redirect("portal:consultar")
    return redirect("monitor:consultar")


def _consultar_json_payload(history: list, *, ok: bool = True, error: str | None = None) -> JsonResponse:
    return JsonResponse(
        sanitize_for_json(
            {
                "ok": ok,
                "error": error,
                "history": history,
            }
        )
    )


def _assistant_payload_from_result(result: dict, pergunta: str) -> dict:
    df = result.get("df")
    resumo = result.get("assistant_text") or ""
    preview = format_consultar_table_html(df, pergunta) if df is not None and not df.empty else ""
    n_rows = len(df) if df is not None else 0
    chart_json = llm_chart_from_df(df) if df is not None and not df.empty else None
    origin = result.get("sql_origin")
    return {
        "role": "assistant",
        "text": resumo,
        "text_html": markdown_to_safe_html(resumo) if resumo else "",
        "sql": result.get("sql"),
        "sql_origin": origin,
        "sql_origin_label": result.get("sql_origin_label")
        or ({"template": "template", "llm": "LLM", "repair": "LLM", "cache": "cache", "vault": "vault"}.get(origin or "", origin)),
        "table_html": preview,
        "chart_json": chart_json,
        "n_rows": n_rows,
        "previsao_url": result.get("previsao_url"),
        "chips": result.get("chips") or [],
        "meta": result.get("meta"),
    }


@require_GET
def consultar_history_api(request):
    from monitor_web.services.raijin import raijin_welcome_message

    history = list(request.session.get("llm_chat_history") or [])
    if not history:
        history = [raijin_welcome_message(lang_pt=get_lang(request) != "en")]
        request.session["llm_chat_history"] = [_slim_chat_entry(m) for m in history]
        request.session.modified = True
        return _consultar_json_payload(history)
    # Só regrava cookie se ainda houver payload pesado (evita corrida HTMX na home).
    slim = [_slim_chat_entry(m) for m in history]
    needs_rewrite = any(
        ("table_html" in m)
        or ("chart_json" in m)
        or ("sql" in m)
        or (len(str(m.get("text") or "")) > 1800)
        for m in history
    ) or len(history) > _CONSULTAR_HISTORY_CAP
    if needs_rewrite:
        if len(slim) > _CONSULTAR_HISTORY_CAP:
            slim = slim[-_CONSULTAR_HISTORY_CAP:]
        request.session["llm_chat_history"] = slim
        request.session.modified = True
        history = slim
    return _consultar_json_payload(history)


@require_POST
def consultar_clear(request):
    from monitor_web.services.raijin import raijin_welcome_message

    welcome = raijin_welcome_message(lang_pt=get_lang(request) != "en")
    request.session["llm_chat_history"] = [welcome]
    request.session["raijin_ctx"] = {}
    if _consultar_wants_json(request):
        return _consultar_json_payload([welcome])
    return _consultar_redirect(request)


def _sse_pack(event: str, data) -> str:
    import json

    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(sanitize_for_json(data), ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@require_POST
def consultar_ask(request):
    wants_json = _consultar_wants_json(request)
    wants_stream = _consultar_wants_stream(request)
    pergunta = (request.POST.get("pergunta") or "").strip()
    if not pergunta:
        if wants_stream:
            return StreamingHttpResponse(
                iter([_sse_pack("error", {"ok": False, "error": "Pergunta vazia."})]),
                content_type="text/event-stream",
            )
        if wants_json:
            return _consultar_json_payload(
                list(request.session.get("llm_chat_history") or []),
                ok=False,
                error="Pergunta vazia.",
            )
        return _consultar_redirect(request)

    def _done(ok: bool = True, error: str | None = None):
        history = list(request.session.get("llm_chat_history") or [])
        if wants_json:
            return _consultar_json_payload(history, ok=ok, error=error)
        return _consultar_redirect(request)

    try:
        from gcp.llm_bq import get_groq_client
        from gcp.bq_config import get_bq_fully_qualified_prefix
        from gcp.monitor_bq_context import monitor_active_bq_dataset
        from monitor_web.services.raijin import parse_intent, process_raijin_question, professional_error
    except Exception as exc:
        _append_consultar_history(
            request,
            {"role": "user", "text": pergunta},
            {"role": "assistant", "text": f"Erro ao carregar módulo LLM: {exc}"},
        )
        return _done(ok=False, error=str(exc))

    client, err = get_groq_client()
    vault_only = parse_intent(pergunta).get("kind") in ("knowledge", "ev_lookup")
    if (not vault_only) and (err or not client):
        err_payload = professional_error("groq", detail=err)
        msg = err_payload["text"]
        _append_consultar_history(
            request,
            {"role": "user", "text": pergunta},
            {
                "role": "assistant",
                "text": msg,
                "text_html": markdown_to_safe_html(msg),
                "chips": err_payload["chips"],
            },
        )
        return _done(ok=False, error=msg)

    ds = monitor_active_bq_dataset()
    prefix = get_bq_fully_qualified_prefix(dataset_id=ds)
    bq_catalog_range = (None, None)
    try:
        from gcp.bq_reader_clima import get_bq_date_range

        bq_catalog_range = get_bq_date_range(dataset=ds)
    except Exception:
        pass

    history_before = list(request.session.get("llm_chat_history") or [])

    if wants_stream:
        import queue
        import threading

        token_q: queue.Queue = queue.Queue()

        def on_step(step: str):
            token_q.put(("step", step))

        def on_token(delta: str):
            token_q.put(("token", delta))

        result_box: dict = {}

        def worker():
            try:
                result_box["result"] = process_raijin_question(
                    request,
                    pergunta,
                    client,
                    table_prefix=prefix,
                    bq_catalog_range=bq_catalog_range,
                    chat_history=history_before,
                    on_step=on_step,
                    on_token=on_token,
                    persist_session=False,
                )
            except Exception as exc:
                result_box["error"] = str(exc)
            finally:
                token_q.put(("done", None))

        def event_stream():
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            yield _sse_pack("step", {"step": "sql", "label": "Preparando consulta…"})
            while True:
                kind, payload = token_q.get()
                if kind == "step":
                    labels = {
                        "sql": "Preparando consulta…",
                        "bq": "Consultando dados do corredor…",
                        "synth": "Sintetizando resposta…",
                    }
                    yield _sse_pack("step", {"step": payload, "label": labels.get(payload, payload)})
                elif kind == "token":
                    yield _sse_pack("token", {"text": payload})
                elif kind == "done":
                    break
            t.join(timeout=120)
            if result_box.get("error"):
                yield _sse_pack("error", {"ok": False, "error": result_box["error"]})
                return
            result = result_box.get("result") or {}
            if result.get("raijin_ctx"):
                request.session["raijin_ctx"] = result["raijin_ctx"]
            if not result.get("ok"):
                err_text = result.get("assistant_text") or "Erro ao processar a pergunta."
                assistant_err = {
                    "role": "assistant",
                    "text": err_text,
                    "text_html": markdown_to_safe_html(err_text),
                    "sql": result.get("sql"),
                    "sql_origin": result.get("sql_origin"),
                    "chips": result.get("chips") or [],
                }
                _append_consultar_history(
                    request,
                    {"role": "user", "text": result.get("user_text", pergunta)},
                    assistant_err,
                )
                try:
                    request.session.save()
                except Exception:
                    pass
                yield _sse_pack(
                    "done",
                    {
                        "ok": False,
                        "error": err_text,
                        "history": _history_for_client(request, rich_last_assistant=assistant_err),
                    },
                )
                return

            user_msg = {"role": "user", "text": result.get("user_text", pergunta)}
            assistant_msg = _assistant_payload_from_result(result, pergunta)
            _append_consultar_history(request, user_msg, assistant_msg)
            try:
                request.session.save()
            except Exception:
                pass
            yield _sse_pack(
                "done",
                {
                    "ok": True,
                    "history": _history_for_client(request, rich_last_assistant=assistant_msg),
                },
            )

        resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        resp["Cache-Control"] = "no-cache"
        resp["X-Accel-Buffering"] = "no"
        return resp

    result = process_raijin_question(
        request,
        pergunta,
        client,
        table_prefix=prefix,
        bq_catalog_range=bq_catalog_range,
        chat_history=history_before,
    )
    if not result.get("ok"):
        err_text = result.get("assistant_text") or "Erro ao processar a pergunta."
        _append_consultar_history(
            request,
            {"role": "user", "text": result.get("user_text", pergunta)},
            {
                "role": "assistant",
                "text": err_text,
                "text_html": markdown_to_safe_html(err_text),
                "sql": result.get("sql"),
                "sql_origin": result.get("sql_origin"),
                "chips": result.get("chips") or [],
            },
        )
        return _done(ok=False, error=err_text)

    user_msg = {"role": "user", "text": result.get("user_text", pergunta)}
    assistant_msg = _assistant_payload_from_result(result, pergunta)
    _append_consultar_history(request, user_msg, assistant_msg)
    return _done(ok=True)


@require_GET
def home_alertas_acumulados_api(request):
    """Alertas acumulados por km no período (lazy; não usado no primeiro paint)."""
    try:
        payload = build_accumulated_alerts_payload(request)
        return JsonResponse(payload)
    except Exception as exc:
        return JsonResponse({"error": str(exc), "markers": []}, status=500)


@require_GET
def home_raijin_alerts_api(request):
    """Últimos alertas Raijin para o painel do sino."""
    try:
        from monitor_web.services.raijin_bell import build_raijin_bell_payload

        return JsonResponse(sanitize_for_json(build_raijin_bell_payload(request)))
    except Exception as exc:
        logger.exception("home_raijin_alerts_api")
        return JsonResponse({"available": False, "error": str(exc), "alerts": []}, status=500)


@require_GET
def home_climate_gfs_api(request):
    """GFS (Open-Meteo): grelha horária nuvem/chuva/vento + frames para replay 2D."""
    try:
        from monitor_web.services.map_climate_gfs import climate_gfs_layer

        step = request.GET.get("step")
        layer = climate_gfs_layer(step_km=int(step) if step else None)
        return JsonResponse(sanitize_for_json(layer))
    except Exception as exc:
        logger.exception("home_climate_gfs_api")
        return JsonResponse(
            {"available": False, "error": str(exc), "points": [], "frames": [], "grid": []},
            status=500,
        )


@require_GET
def home_goto_km_api(request):
    """Lat/lon do km EFC para o mapa (Ir para)."""
    try:
        from monitor_web.services.map_climate_gfs import km_lat_lon

        km_raw = request.GET.get("km")
        if km_raw is None or str(km_raw).strip() == "":
            return JsonResponse({"ok": False, "error": "km required"}, status=400)
        payload = km_lat_lon(float(km_raw))
        payload["ok"] = payload.get("lat") is not None
        return JsonResponse(sanitize_for_json(payload))
    except Exception as exc:
        logger.exception("home_goto_km_api")
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@require_GET
def bq_warmup(request):
    """Cron/Scheduler: aquece cache BQ. Header X-Warmup-Secret ou ?secret=."""
    import os

    expected = (os.environ.get("MONITOR_WARMUP_SECRET") or "").strip()
    if not expected:
        return JsonResponse({"ok": False, "error": "warmup disabled"}, status=503)
    got = (request.headers.get("X-Warmup-Secret") or request.GET.get("secret") or "").strip()
    if got != expected:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    from monitor_web.services.bq_warmup import warm_portal_bq_caches

    skip_map = (request.GET.get("skip_map") or "").strip().lower() in ("1", "true", "yes")
    result = warm_portal_bq_caches(warm_map=not skip_map)
    return JsonResponse(result)


@require_GET
def health(request):
    return HttpResponse("ok", content_type="text/plain")
