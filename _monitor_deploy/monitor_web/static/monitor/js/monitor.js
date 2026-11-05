(function () {
  var loadPct = 8;

  function debounce(fn, ms) {
    var timer;
    return function () {
      var ctx = this;
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(ctx, args);
      }, ms);
    };
  }

  var monitorAuthRedirecting = false;

  function monitorLoginRedirectUrl() {
    var base = document.body.getAttribute("data-monitor-login-url") || "/login/";
    var next = encodeURIComponent(window.location.pathname + window.location.search);
    var join = base.indexOf("?") >= 0 ? "&" : "?";
    return base + join + "next=" + next;
  }

  function responseLooksLikeLoginPage(text) {
    if (!text || typeof text !== "string") return false;
    return text.indexOf("efc-login-card") !== -1 || text.indexOf("efc-login-body") !== -1;
  }

  function redirectToMonitorLoginOnce() {
    if (monitorAuthRedirecting) return;
    monitorAuthRedirecting = true;
    window.location.replace(monitorLoginRedirectUrl());
  }

  function initMonitorAuthRedirects() {
    function isHomeFragmentTarget(target) {
      if (!target || !target.id) return false;
      return (
        target.id === "home-map-panel" ||
        target.id === "home-indicators" ||
        target.id === "home-day-strip" ||
        target.id === "home-alerts-slot" ||
        target.id === "home-exceed-slot" ||
        target.id === "portal-home-bootstrap"
      );
    }

    document.body.addEventListener("htmx:beforeSwap", function (ev) {
      var xhr = ev.detail && ev.detail.xhr;
      var target = ev.detail && ev.detail.target;
      if (!xhr) return;
      var homeFrag = isHomeFragmentTarget(target);
      var looksLogin = responseLooksLikeLoginPage(xhr.responseText);
      // Troca de data: um fragmento 401/login NÃO pode deslogar a página inteira.
      if (homeFrag && (xhr.status === 401 || looksLogin)) {
        ev.detail.shouldSwap = true;
        return;
      }
      if (xhr.status === 401 || looksLogin) {
        ev.preventDefault();
        redirectToMonitorLoginOnce();
      }
    });
    document.body.addEventListener("htmx:responseError", function (ev) {
      var xhr = ev.detail && ev.detail.xhr;
      var target = ev.detail && ev.detail.target;
      if (!xhr || xhr.status !== 401) return;
      if (isHomeFragmentTarget(target)) return;
      redirectToMonitorLoginOnce();
    });
  }

  function setLoadPct(pct) {
    loadPct = Math.min(100, Math.max(8, pct));
    var bar = document.getElementById("efc-load-progress");
    if (bar) {
      bar.style.setProperty("--efc-load-pct", loadPct + "%");
      bar.setAttribute("aria-valuenow", String(Math.round(loadPct)));
    }
    document.documentElement.style.setProperty("--efc-load-pct", loadPct + "%");
  }

  function parsePlotJson(raw) {
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e1) {
      try {
        var ta = document.createElement("textarea");
        ta.innerHTML = raw;
        return JSON.parse(ta.value);
      } catch (e2) {
        return null;
      }
    }
  }

  function plotlyReady() {
    return typeof Plotly !== "undefined" && typeof Plotly.newPlot === "function";
  }

  var _plotlyLoadPromise = null;
  function ensurePlotlyLoaded() {
    if (plotlyReady()) return Promise.resolve();
    if (_plotlyLoadPromise) return _plotlyLoadPromise;
    _plotlyLoadPromise = new Promise(function (resolve, reject) {
      var existing = document.querySelector('script[data-efc-plotly="1"]');
      if (existing) {
        existing.addEventListener("load", function () { resolve(); });
        existing.addEventListener("error", function () { reject(new Error("Plotly load failed")); });
        if (plotlyReady()) resolve();
        return;
      }
      var s = document.createElement("script");
      s.src = "https://cdn.plot.ly/plotly-2.35.2.min.js";
      s.async = true;
      s.setAttribute("data-efc-plotly", "1");
      s.onload = function () { resolve(); };
      s.onerror = function () {
        _plotlyLoadPromise = null;
        reject(new Error("Plotly CDN failed"));
      };
      document.head.appendChild(s);
    });
    return _plotlyLoadPromise;
  }

  function plotlyConfig(el) {
    var cfg = { responsive: true, displayModeBar: false, scrollZoom: true, doubleClick: "reset" };
    if (el && el.classList && el.classList.contains("efc-previsao-heatmap-plot")) {
      cfg.scrollZoom = !previsaoHeatmapCompact(el);
    }
    return cfg;
  }

  function previsaoHeatmapCompact(el) {
    var w = (el && (el.clientWidth || el.offsetWidth)) || 0;
    if (w < 40) w = window.innerWidth || 0;
    return w > 0 && w < 640;
  }

  function previsaoHeatmapThinKmTicks(layout) {
    var xa = (layout && layout.xaxis) || {};
    var vals = xa.tickvals || [];
    var texts = xa.ticktext || [];
    if (vals.length <= 6) return;
    var keep = [];
    var keepT = [];
    var step = Math.max(1, Math.ceil(vals.length / 5));
    for (var i = 0; i < vals.length; i += step) {
      keep.push(vals[i]);
      keepT.push(texts[i] != null ? texts[i] : String(vals[i]));
    }
    if (keep[keep.length - 1] !== vals[vals.length - 1]) {
      keep.push(vals[vals.length - 1]);
      keepT.push(texts[texts.length - 1]);
    }
    xa.tickvals = keep;
    xa.ticktext = keepT;
  }

  function previsaoHeatmapTickLabel(v) {
    var n = Number(v);
    if (!isFinite(n)) return "";
    if (Math.abs(n - Math.round(n)) < 1e-6) return String(Math.round(n));
    return String(Math.round(n * 10) / 10);
  }

  function previsaoHeatmapTickvals(zmin, zmax) {
    var lo = Math.max(0, Number(zmin) || 0);
    var hi = Math.max(Number(zmax) || 1, lo + 0.1);
    if (!isFinite(lo)) lo = 0;
    if (!isFinite(hi)) hi = lo + 1;
    var span = hi - lo;
    var step = span <= 2 ? 0.5 : span <= 6 ? 1 : span <= 15 ? 2 : span <= 40 ? 5 : span <= 80 ? 10 : 20;
    var ticks = [];
    var t = lo <= 0 ? 0 : Math.ceil(lo / step) * step;
    for (; t <= hi + 1e-9; t += step) {
      ticks.push(Math.round(t * 100) / 100);
    }
    if (!ticks.length || ticks[0] > lo + 1e-9) {
      ticks.unshift(Math.round(lo * 100) / 100);
    }
    var last = Math.round(hi * 100) / 100;
    if (ticks[ticks.length - 1] < last - 1e-9) {
      ticks.push(last);
    }
    var out = [];
    ticks.forEach(function (tv) {
      if (!out.length || Math.abs(out[out.length - 1] - tv) > 1e-9) out.push(tv);
    });
    return out.length >= 2 ? out : [lo, hi];
  }

  function buildPrevisaoHeatmapColorbarConfig(zmin, zmax, compact) {
    var ticks = previsaoHeatmapTickvals(zmin, zmax);
    var step = ticks.length >= 2 ? ticks[1] - ticks[0] : Math.max(zmax - zmin, 1);
    return {
      title: {
        text: "mm/24 h",
        side: "right",
        font: { size: compact ? 10 : 11, color: "#f1f5f9" },
      },
      tickmode: "linear",
      tick0: ticks[0],
      dtick: step,
      tickformat: step < 1 ? ".1f" : ".0f",
      exponentformat: "none",
      separatethousands: false,
      thickness: compact ? 20 : 24,
      len: 0.88,
      x: 1.012,
      xpad: compact ? 8 : 10,
      ypad: 12,
      y: 0.5,
      yanchor: "middle",
      outlinewidth: 1,
      outlinecolor: "rgba(148, 163, 184, 0.85)",
      showticklabels: true,
      ticks: "outside",
      ticklen: 6,
      tickwidth: 1,
      tickcolor: "#ffffff",
      tickfont: { size: compact ? 13 : 14, color: "#ffffff" },
    };
  }

  function applyPrevisaoHeatmapColorbar(data0, compact, layout) {
    if (!data0) return;
    var zmin = data0.zmin != null ? Number(data0.zmin) : 0;
    var zmax = data0.zmax != null ? Number(data0.zmax) : 1;
    if (!isFinite(zmin)) zmin = 0;
    if (!isFinite(zmax) || zmax <= zmin) zmax = zmin + 1;
    var cb = buildPrevisaoHeatmapColorbarConfig(zmin, zmax, compact);
    data0.showscale = true;
    if (layout) {
      layout.coloraxis = Object.assign({}, layout.coloraxis || {}, {
        cmin: zmin,
        cmax: zmax,
        colorbar: cb,
      });
      data0.coloraxis = "coloraxis";
      delete data0.colorbar;
      return;
    }
    data0.colorbar = cb;
  }

  function fixPrevisaoHeatmapColorbarLabels(el) {
    if (!el || !el.data || !el.data[0]) return;
    var d0 = el.data[0];
    var zmin = d0.zmin != null ? Number(d0.zmin) : 0;
    var zmax = d0.zmax != null ? Number(d0.zmax) : 1;
    var cb = buildPrevisaoHeatmapColorbarConfig(zmin, zmax, previsaoHeatmapCompact(el));
    Plotly.relayout(el, {
      "coloraxis.cmin": zmin,
      "coloraxis.cmax": zmax,
      "coloraxis.colorbar.tickmode": cb.tickmode,
      "coloraxis.colorbar.tick0": cb.tick0,
      "coloraxis.colorbar.dtick": cb.dtick,
      "coloraxis.colorbar.tickformat": cb.tickformat,
      "coloraxis.colorbar.showticklabels": true,
    });
  }

  function applyPrevisaoHeatmapCompactToFig(fig) {
    var out = JSON.parse(JSON.stringify(fig));
    var data0 = (out.data && out.data[0]) || {};
    var nDays = (data0.y && data0.y.length) || 20;
    var h = Math.max(520, Math.min(760, nDays * 26 + 78));
    out.layout = out.layout || {};
    out.layout.height = h;
    out.layout.autosize = true;
    out.layout.margin = { l: 44, r: 92, t: 8, b: 40, pad: 0 };
    out.layout.showlegend = false;
    out.layout.font = Object.assign({}, out.layout.font || {}, { size: 11 });
    out.layout.xaxis = out.layout.xaxis || {};
    out.layout.yaxis = out.layout.yaxis || {};
    out.layout.xaxis.title = "";
    out.layout.yaxis.title = "";
    out.layout.xaxis.tickangle = 0;
    out.layout.xaxis.automargin = false;
    out.layout.yaxis.automargin = false;
    out.layout.xaxis.tickfont = Object.assign({}, out.layout.xaxis.tickfont || {}, { size: 12 });
    out.layout.yaxis.tickfont = Object.assign({}, out.layout.yaxis.tickfont || {}, { size: 12 });
    previsaoHeatmapThinKmTicks(out.layout);
    var anns = out.layout.annotations || [];
    anns.forEach(function (a) {
      if (a && String(a.text || "").indexOf("Hoje") >= 0) {
        a.font = Object.assign({}, a.font || {}, { size: 11 });
        a.xanchor = "right";
        a.xshift = -6;
      }
    });
    applyPrevisaoHeatmapColorbar(data0, true, out.layout);
    return out;
  }

  var _previsaoHeatmapResizeTimer = null;
  function bindPrevisaoHeatmapResize(el) {
    if (!el || el.getAttribute("data-hm-resize") === "1") return;
    el.setAttribute("data-hm-resize", "1");
    window.addEventListener("resize", function () {
      clearTimeout(_previsaoHeatmapResizeTimer);
      _previsaoHeatmapResizeTimer = setTimeout(function () {
        if (!el.isConnected || !el._efcHeatmapFig) return;
        paintPrevisaoHeatmap(el);
      }, 180);
    });
  }

  function paintPrevisaoHeatmap(el) {
    var src = el && el._efcHeatmapFig;
    if (!el || !src || !plotlyReady()) return false;
    var compact = previsaoHeatmapCompact(el);
    var fig = compact ? applyPrevisaoHeatmapCompactToFig(src) : JSON.parse(JSON.stringify(src));
    if (fig.data && fig.data[0]) {
      applyPrevisaoHeatmapColorbar(fig.data[0], compact, layout);
    }
    var layout = fig.layout || {};
    layout.paper_bgcolor = layout.paper_bgcolor || "rgba(0,0,0,0)";
    layout.plot_bgcolor = layout.plot_bgcolor || "rgba(0,0,0,0)";
    layout.font = layout.font || { color: "#e2e8f0", size: 11 };
    if (!compact) {
      layout.margin = Object.assign({}, layout.margin || {}, { r: 104, t: 24, b: 48 });
    }
    if (!fig.data || !fig.data.length) return false;
    if (compact && layout.height) {
      el.style.minHeight = layout.height + "px";
      el.style.height = layout.height + "px";
    } else {
      el.style.minHeight = "";
      el.style.height = "";
    }
    Plotly.newPlot(el, fig.data, layout, plotlyConfig(el)).then(function () {
      fixPrevisaoHeatmapColorbarLabels(el);
    });
    el.setAttribute("data-plot-rendered", "1");
    return true;
  }

  function renderOneChart(el, fig) {
    if (!el || !fig || !plotlyReady()) return false;
    if (el.classList && el.classList.contains("efc-previsao-heatmap-plot")) {
      el._efcHeatmapFig = fig;
      bindPrevisaoHeatmapResize(el);
      return paintPrevisaoHeatmap(el);
    }
    var layout = fig.layout || {};
    layout.paper_bgcolor = layout.paper_bgcolor || "rgba(0,0,0,0)";
    layout.plot_bgcolor = layout.plot_bgcolor || "rgba(0,0,0,0)";
    layout.font = layout.font || { color: "#e2e8f0", size: 11 };
    layout.margin = layout.margin || { l: 48, r: 24, t: 36, b: 48 };
    if (!fig.data || !fig.data.length) return false;
    Plotly.newPlot(el, fig.data, layout, plotlyConfig(el));
    el.setAttribute("data-plot-rendered", "1");
    return true;
  }

  function plotScriptVisible(scriptEl) {
    var det = scriptEl.closest("details");
    return !det || det.open;
  }

  function renderPlotElement(el, fig) {
    if (!el || !fig || !plotlyReady()) return false;
    if (el.classList && el.classList.contains("efc-previsao-heatmap-plot")) {
      el._efcHeatmapFig = fig;
      bindPrevisaoHeatmapResize(el);
      return paintPrevisaoHeatmap(el);
    }
    var layout = fig.layout || {};
    layout.paper_bgcolor = layout.paper_bgcolor || "rgba(0,0,0,0)";
    layout.plot_bgcolor = layout.plot_bgcolor || "rgba(0,0,0,0)";
    layout.font = layout.font || { color: "#e2e8f0", size: 11 };
    layout.margin = layout.margin || { l: 48, r: 24, t: 36, b: 48 };
    if (!fig.data || !fig.data.length) return false;
    Plotly.newPlot(el, fig.data, layout, plotlyConfig(el));
    el.setAttribute("data-plot-rendered", "1");
    return true;
  }

  function renderPlots(root) {
    if (!plotlyReady()) return;
    var scope = root || document;
    scope.querySelectorAll("script.efc-plot-data[type='application/json']").forEach(function (scriptEl) {
      if (!plotScriptVisible(scriptEl)) return;
      var targetId = scriptEl.getAttribute("data-for");
      if (!targetId) return;
      var el = document.getElementById(targetId);
      if (!el || el.getAttribute("data-plot-rendered") === "1") return;
      var fig = parsePlotJson(scriptEl.textContent);
      if (!fig) return;
      try {
        renderPlotElement(el, fig);
      } catch (e) {
        console.warn("Plotly render failed", e);
      }
    });
    scope.querySelectorAll("[data-plot]").forEach(function (el) {
      if (el.getAttribute("data-plot-rendered") === "1") return;
      var raw = el.getAttribute("data-plot");
      if (!raw) return;
      try {
        var fig = parsePlotJson(raw);
        if (!fig) return;
        renderPlotElement(el, fig);
      } catch (e) {
        console.warn("Plotly render failed", e);
      }
    });
  }

  function leafletMapInWindow(win, doc) {
    if (!win || !doc || !win.L) return null;
    var mapDiv = doc.querySelector("[id^='map_']") || doc.querySelector(".folium-map");
    if (mapDiv && mapDiv.id && win[mapDiv.id] && typeof win[mapDiv.id].addLayer === "function") {
      return win[mapDiv.id];
    }
    var key;
    for (key in win) {
      if (key.indexOf("map_") === 0 && win[key] && typeof win[key].addLayer === "function") {
        return win[key];
      }
    }
    return null;
  }

  /** Resolve Leaflet map in parent page, HTMX embed iframe, and nested Folium srcdoc iframes. */
  function findLeafletMap(root) {
    if (!root) return null;

    function searchDoc(win, doc, depth) {
      if (!win || !doc || depth > 5) return null;
      var found = leafletMapInWindow(win, doc);
      if (found) return found;
      var iframes = doc.querySelectorAll("iframe");
      var i;
      for (i = 0; i < iframes.length; i++) {
        try {
          var childWin = iframes[i].contentWindow;
          var childDoc = iframes[i].contentDocument || (childWin && childWin.document);
          found = searchDoc(childWin, childDoc, depth + 1);
          if (found) return found;
        } catch (e) {
          /* cross-origin */
        }
      }
      return null;
    }

    var outerIframe = root.querySelector("iframe");
    if (outerIframe) {
      try {
        var oWin = outerIframe.contentWindow;
        var oDoc = outerIframe.contentDocument || (oWin && oWin.document);
        var map = searchDoc(oWin, oDoc, 0);
        if (map) return map;
      } catch (e) {
        /* ignore */
      }
    }

    return searchDoc(window, document, 0);
  }

  var accAlertsBoundControls = null;
  var accAlertsMapApi = null;
  var mapCaptionBound = false;

  function formatDateBr(iso) {
    if (!iso) return "";
    var p = String(iso).split("-");
    if (p.length === 3) return p[2] + "/" + p[1] + "/" + p[0];
    return iso;
  }

  function accumHoursToDays(hours) {
    var h = parseInt(hours, 10);
    if (!isFinite(h) || h <= 0) h = 720;
    if (h <= 24) return 1;
    if (h <= 120) return 5;
    if (h <= 168) return 7;
    return 30;
  }

  function readPeriodState(controls) {
    if (!controls) {
      return { mode: "30", days: "30", start: "", end: "", periodVal: "days_30" };
    }
    var modeSel = controls.querySelector("#efc-period-mode");
    var mode = modeSel ? modeSel.value : "30";
    var startIn = controls.querySelector("#efc-period-start");
    var endIn = controls.querySelector("#efc-period-end");
    var start = startIn && startIn.value ? startIn.value : "";
    var end = endIn && endIn.value ? endIn.value : "";
    if (mode === "custom" && start && end) {
      return {
        mode: "custom",
        start: start,
        end: end,
        periodVal: "range_" + start + "_" + end,
      };
    }
    var form =
      document.getElementById("efc-home-toolbar-form") ||
      document.querySelector(".efc-date-form");
    var days = String(accumHoursToDays(readToolbarAccumHours(form)));
    return { mode: "30", days: days, start: start, end: end, periodVal: "days_" + days };
  }

  function setCustomPeriodVisible(controls, visible) {
    var wrap = controls && controls.querySelector("#efc-period-custom");
    if (!wrap) return;
    wrap.classList.toggle("is-visible", !!visible);
    wrap.hidden = !visible;
  }

  function syncPeriodDaysHidden(controls) {
    var daysHidden = controls && controls.querySelector("#efc-period-days");
    var modeSel = controls && controls.querySelector("#efc-period-mode");
    if (!daysHidden || !modeSel) return;
    if (modeSel.value === "custom") {
      daysHidden.disabled = true;
      daysHidden.value = "";
    } else {
      daysHidden.disabled = false;
      var form =
        document.getElementById("efc-home-toolbar-form") ||
        document.querySelector(".efc-date-form");
      daysHidden.value = String(accumHoursToDays(readToolbarAccumHours(form)));
    }
  }

  function syncHomePeriodToUrl(controls) {
    if (!controls) return;
    var params = new URLSearchParams(window.location.search);
    var st = readPeriodState(controls);
    params.delete("days");
    params.delete("start");
    params.delete("end");
    params.delete("date_start");
    params.delete("date_end");
    params.delete("period");
    params.delete("period_mode");
    if (st.mode === "custom" && st.start && st.end) {
      params.set("period_mode", "custom");
      params.set("start", st.start);
      params.set("end", st.end);
      params.set("period", st.periodVal);
    } else {
      var days = st.days || "30";
      params.set("days", days);
      params.set("period", "days_" + days);
    }
    var qs = params.toString();
    var next = window.location.pathname + (qs ? "?" + qs : "");
    var cur = window.location.pathname + window.location.search;
    if (next !== cur) {
      history.replaceState(null, "", next);
    }
  }

  function applyPeriodFromUrl(controls) {
    if (!controls) return;
    var params = new URLSearchParams(window.location.search);
    var modeSel = controls.querySelector("#efc-period-mode");
    var startIn = controls.querySelector("#efc-period-start");
    var endIn = controls.querySelector("#efc-period-end");
    var period = params.get("period") || "";
    var start = params.get("start") || params.get("date_start") || "";
    var end = params.get("end") || params.get("date_end") || "";
    if (!start && !end && period.indexOf("range_") === 0) {
      var parts = period.replace(/^range_/, "").split("_");
      if (parts.length >= 2) {
        start = parts[0];
        end = parts.slice(1).join("_");
      }
    }
    if ((params.get("period_mode") === "custom" || (start && end)) && modeSel) {
      modeSel.value = "custom";
      if (startIn && start) startIn.value = start;
      if (endIn && end) endIn.value = end;
      setCustomPeriodVisible(controls, true);
    } else if (modeSel) {
      modeSel.value = "30";
      setCustomPeriodVisible(controls, false);
    }
    syncPeriodDaysHidden(controls);
  }

  function accPeriodPhrase(periodVal, langPt) {
    if (periodVal && typeof periodVal === "object") {
      var st = periodVal;
      if (st.mode === "custom" && st.start && st.end) {
        return langPt
          ? formatDateBr(st.start) + " – " + formatDateBr(st.end)
          : st.start + " – " + st.end;
      }
      periodVal = st.periodVal || "30";
    }
    var v = periodVal || "30";
    if (String(v).indexOf("range_") === 0) {
      var rp = String(v).replace(/^range_/, "").split("_");
      if (rp.length >= 2) {
        return langPt
          ? formatDateBr(rp[0]) + " – " + formatDateBr(rp.slice(1).join("_"))
          : rp[0] + " – " + rp.slice(1).join("_");
      }
    }
    if (v.indexOf("year_") === 0) {
      var yr = v.replace(/^year_/, "").split("_")[0];
      return langPt ? "ano " + yr : "year " + yr;
    }
    var days = String(v).replace(/^days_/, "");
    if (!days || days === v) days = v === "30" ? "30" : v;
    return langPt ? "últimos " + days + " dias" : "last " + days + " days";
  }

  function mapObservationSummary(opts) {
    var langPt = !!opts.langPt;
    var dateDisp =
      opts.dateDisplay || formatDateBr(opts.dateIso) || (langPt ? "—" : "—");
    var hours = opts.accumHours || 720;
    var accum =
      hours === 24 ? "24 h" : hours === 120 ? "5 d" : hours === 168 ? "7 d" : "30 d";
    if (langPt) {
      var basePt = dateDisp + " · janela " + accum + " · estações BNDMET/ANA";
      if (opts.useBq && opts.accOn !== false) {
        return basePt + " · alertas acumulados · " + accPeriodPhrase(opts.periodVal, true);
      }
      return basePt;
    }
    var baseEn = dateDisp + " · " + accum + " window · BNDMET/ANA stations";
    if (opts.useBq && opts.accOn !== false) {
      return baseEn + " · accumulated alerts · " + accPeriodPhrase(opts.periodVal, false);
    }
    return baseEn;
  }

  function readToolbarAccumHours(form) {
    var root = form || document;
    var checked = root.querySelector('input[name="accum"]:checked');
    if (!checked) return 720;
    var v = String(checked.value || "");
    if (v === "24") return 24;
    if (v === "120" || v === "5") return 120;
    if (v === "168" || v === "7") return 168;
    if (v === "720" || v === "30") return 720;
    var n = parseInt(v, 10);
    return n === 24 || n === 120 || n === 168 || n === 720 ? n : 720;
  }

  function updateMapObservationCaption() {
    var label = document.getElementById("efc-map-observation-label");
    if (!label) return;
    var controls = document.getElementById("efc-acc-alerts-controls");
    var form = document.getElementById("efc-home-toolbar-form");
    var dateIn =
      (form && form.querySelector('input[name="date"]')) || document.getElementById("date");
    var langPt = !controls || (controls.getAttribute("data-lang") || "pt") === "pt";
    var useBq = !!controls;
    var accOn = true;
    var periodVal = "30";
    if (controls) {
      var toggle = controls.querySelector("#efc-acc-alerts-enabled");
      accOn = toggle ? toggle.checked : true;
      periodVal = readPeriodState(controls);
      langPt = (controls.getAttribute("data-lang") || "pt") === "pt";
    }
    var accumHours = readToolbarAccumHours(form);
    var dateIso = readToolbarDateIso(form) || (dateIn && dateIn.value ? dateIn.value : "");
    var dateDisplay = "";
    if (dateIso) dateDisplay = formatDateBr(dateIso);
    else dateDisplay = label.getAttribute("data-date-display") || "";
    if (dateDisplay) label.setAttribute("data-date-display", dateDisplay);
    label.textContent = mapObservationSummary({
      langPt: langPt,
      dateIso: dateIso,
      dateDisplay: dateDisplay,
      accumHours: accumHours,
      useBq: useBq,
      accOn: accOn,
      periodVal: periodVal,
    });
  }

  var MAP_IFRAME_TIMEOUT_MS_DEFAULT = 95000;

  function mapLangPt() {
    var controls = document.getElementById("efc-acc-alerts-controls");
    if (controls) return (controls.getAttribute("data-lang") || "pt") === "pt";
    return (document.documentElement.lang || "pt").indexOf("en") < 0;
  }

  function mapLoadTimeoutMessage(langPt) {
    return langPt
      ? "O mapa demorou demais para carregar. Atualize a página ou tente outra data."
      : "The map took too long to load. Refresh the page or try another date.";
  }

  function showMapLoadError(mapFrame, langPt) {
    if (!mapFrame || mapFrame.querySelector(".efc-map-load-error")) return;
    var overlay = document.getElementById("efc-map-skeleton-overlay");
    if (overlay) overlay.classList.add("is-hidden");
    var p = document.createElement("p");
    p.className = "efc-map-unavailable efc-map-load-error";
    p.setAttribute("role", "alert");
    p.textContent = mapLoadTimeoutMessage(langPt);
    mapFrame.appendChild(p);
  }

  function isPortalGraphiteHome() {
    return document.body.classList.contains("efc-ui-graphite") && !!document.getElementById("efc-home-main");
  }

  var portalCacheRetryTimer = null;
  var portalCacheRetryCount = 0;
  var PORTAL_CACHE_RETRY_MAX = 8;
  var PORTAL_CACHE_RETRY_MS = 3500;

  function withForceParam(url) {
    if (!url) return url;
    if (/([?&])force=/.test(url)) return url;
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "force=1";
  }

  function schedulePortalCacheRetry(boot) {
    if (!boot || portalCacheRetryCount >= PORTAL_CACHE_RETRY_MAX) return;
    if (portalCacheRetryTimer) return;
    var indSlot = document.getElementById("home-indicators");
    var needsRetry =
      indSlot &&
      (indSlot.querySelector(".dj-cache-wait") ||
        indSlot.querySelector("[data-efc-indicators-retry]") ||
        (indSlot.textContent || "").indexOf("sincronização") >= 0 ||
        (indSlot.textContent || "").indexOf("syncing") >= 0 ||
        (indSlot.textContent || "").indexOf("Carregando indicadores") >= 0 ||
        (indSlot.textContent || "").indexOf("Loading indicators") >= 0);
    if (!needsRetry) return;
    portalCacheRetryTimer = window.setTimeout(function () {
      portalCacheRetryTimer = null;
      portalCacheRetryCount += 1;
      var indUrl = boot.getAttribute("data-indicators-url");
      if (indUrl && typeof htmx !== "undefined" && htmx.ajax) {
        // A partir do 1º retry, força carga BQ (com timeout no servidor).
        var url = portalCacheRetryCount >= 1 ? withForceParam(indUrl) : indUrl;
        htmx.ajax("GET", url, { target: "#home-indicators", swap: "innerHTML", timeout: 120000 });
      }
      schedulePortalCacheRetry(boot);
    }, PORTAL_CACHE_RETRY_MS);
  }

  function scheduleStitchIndicatorsRetry() {
    var boot = document.getElementById("efc-stitch-home-boot");
    if (!boot || isPortalGraphiteHome()) return;
    schedulePortalCacheRetry(boot);
  }

  function kickStitchIndicatorsForce() {
    // First paint já vem lite (rápido). Enrich BQ pesado fica só no warmup/cron.
    return;
  }

  function loadPortalMapPanel(boot) {
    if (!boot || typeof htmx === "undefined" || !htmx.ajax) return;
    if (boot.getAttribute("data-map-loading") === "1") return;
    var mapUrl = boot.getAttribute("data-map-url");
    var panel = document.getElementById("home-map-panel");
    if (!mapUrl || !panel) return;
    mapUrl = appendSuscPrefToUrl(mapUrl);
    boot.setAttribute("data-map-url", mapUrl);
    boot.setAttribute("data-map-loading", "1");
    htmx.ajax("GET", mapUrl, {
      target: "#home-map-panel",
      swap: "innerHTML",
      source: panel,
      timeout: 180000,
    });
  }

  /** Portal: indicadores + mapa no load (em paralelo com bootstrap OOB). */
  function startPortalHomeParallelLoads() {
    if (!isPortalGraphiteHome()) return;
    var boot = document.getElementById("portal-home-bootstrap");
    if (!boot || typeof htmx === "undefined" || !htmx.ajax) return;
    if (boot.getAttribute("data-parallel-started") === "1") return;
    boot.setAttribute("data-parallel-started", "1");
    var indUrl = boot.getAttribute("data-indicators-url");
    if (indUrl) {
      boot.setAttribute("data-indicators-loading", "1");
      htmx.ajax("GET", indUrl, {
        target: "#home-indicators",
        swap: "innerHTML",
        timeout: 120000,
      });
    }
    loadPortalMapPanel(boot);
  }

  function loadDeferredPortalMapIframe() {
    var iframe = document.getElementById("efc-map-iframe");
    if (!iframe) return;
    var url = iframe.getAttribute("data-embed-src") || iframe.getAttribute("src");
    if (!url || url === "about:blank") return;
    if (iframe.getAttribute("data-embed-loaded") === "1" && iframe.src && iframe.src !== "about:blank") {
      return;
    }
    url = appendSuscPrefToUrl(url);
    iframe.setAttribute("data-embed-src", url);
    iframe.setAttribute("data-embed-loaded", "1");
    iframe.src = url;
  }

  /** Força reload do Folium (evita iframe/cache preso na data anterior). */
  function forceMapIframeReload(root) {
    var panel = root || document.getElementById("home-map-panel");
    if (!panel) return;
    var iframe = panel.querySelector("#efc-map-iframe, iframe.efc-map-iframe, iframe");
    if (!iframe) return;
    var raw = iframe.getAttribute("data-embed-src") || iframe.getAttribute("src") || "";
    raw = String(raw).replace(/&amp;/g, "&");
    if (!raw || raw === "about:blank") return;
    var busted = appendSuscPrefToUrl(upsertQueryParam(raw, "_", String(Date.now())));
    iframe.removeAttribute("data-embed-loaded");
    iframe.removeAttribute("data-timeout-bound");
    iframe.setAttribute("data-embed-src", busted);
    try {
      iframe.src = "about:blank";
    } catch (e) { /* ignore */ }
    window.setTimeout(function () {
      iframe.src = busted;
      iframe.setAttribute("data-embed-loaded", "1");
      initMapIframeTimeout();
      scheduleSuscLayerRestore();
    }, 30);
  }

  function initMapLegendCollapse() {
    var legend = document.getElementById("efc-map-legend");
    var toggle = document.getElementById("efc-map-legend-toggle");
    if (!legend || !toggle || toggle.dataset.legendBound === "1") return;
    toggle.dataset.legendBound = "1";
    var storageKey = "efc_map_legend_collapsed";
    var collapsed = false;
    try {
      var stored = localStorage.getItem(storageKey);
      if (stored === "1") collapsed = true;
      else if (stored === "0") collapsed = false;
      else collapsed = window.matchMedia("(max-width: 640px)").matches;
    } catch (e) {
      collapsed = window.matchMedia("(max-width: 640px)").matches;
    }
    function applyState() {
      legend.classList.toggle("is-collapsed", collapsed);
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      toggle.title = collapsed
        ? mapLangPt()
          ? "Expandir legenda"
          : "Expand legend"
        : mapLangPt()
          ? "Recolher legenda"
          : "Collapse legend";
    }
    applyState();
    toggle.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      collapsed = !collapsed;
      try {
        localStorage.setItem(storageKey, collapsed ? "1" : "0");
      } catch (e) {}
      applyState();
    });
  }

  /** Persist Suscetibilidade overlay across Folium iframe reloads (date/accum change). */
  var SUSC_LAYER_STORAGE_KEY = "efc_map_suscetibilidade_on";
  var _suscLayerPersistBound = false;
  var _suscLayerRestoreTimer = null;
  var _suscLayerObserver = null;
  var _suscLayerObserverDoc = null;

  function isSuscLayerName(name) {
    var n = String(name || "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
    return n.indexOf("suscetibilidade") >= 0 || n.indexOf("susceptibility") >= 0;
  }

  function readSuscLayerPref() {
    try {
      return localStorage.getItem(SUSC_LAYER_STORAGE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function writeSuscLayerPref(on) {
    try {
      localStorage.setItem(SUSC_LAYER_STORAGE_KEY, on ? "1" : "0");
    } catch (e) {
      /* private mode */
    }
  }

  /** Append ?susc=1|0 so the server renders FeatureGroup(show=…) correctly after date swap. */
  function appendSuscPrefToUrl(url) {
    if (!url) return url;
    return upsertQueryParam(String(url).replace(/&amp;/g, "&"), "susc", readSuscLayerPref() ? "1" : "0");
  }

  function suscMapDoc(map) {
    if (!map || typeof map.getContainer !== "function") return null;
    var container = map.getContainer();
    return container && container.ownerDocument ? container.ownerDocument : null;
  }

  function findSuscOverlayCheckbox(map) {
    var doc = suscMapDoc(map);
    if (!doc) return null;
    var labels = doc.querySelectorAll(
      ".leaflet-control-layers-overlays label, .leaflet-control-layers label"
    );
    var i;
    for (i = 0; i < labels.length; i++) {
      if (isSuscLayerName(labels[i].textContent)) {
        return labels[i].querySelector('input[type="checkbox"]');
      }
    }
    return null;
  }

  /**
   * Folium keeps LayerControl in a block-scoped `let`, but Leaflet stamps
   * `input.layerId`. Resolve overlay via control._layers when discoverable,
   * else via map._efcSuscLayer cached from overlayadd/remove.
   */
  function findSuscOverlayLayer(map) {
    if (!map) return null;
    if (map._efcSuscLayer) return map._efcSuscLayer;
    var input = findSuscOverlayCheckbox(map);
    var layerId = input && input.layerId != null ? input.layerId : null;
    var doc = suscMapDoc(map);
    var win = doc && doc.defaultView;
    if (!win || layerId == null) return null;
    var k;
    for (k in win) {
      try {
        var cand = win[k];
        if (!cand || cand._map !== map || !Array.isArray(cand._layers)) continue;
        if (typeof cand._getLayer !== "function" && typeof cand._update !== "function") continue;
        var li;
        for (li = 0; li < cand._layers.length; li++) {
          var entry = cand._layers[li];
          if (!entry || !entry.overlay || !entry.layer) continue;
          if (isSuscLayerName(entry.name)) {
            map._efcSuscLayer = entry.layer;
            return entry.layer;
          }
          if (win.L && win.L.Util && win.L.Util.stamp(entry.layer) === layerId) {
            map._efcSuscLayer = entry.layer;
            return entry.layer;
          }
        }
      } catch (eScan) { /* ignore */ }
    }
    return null;
  }

  function setSuscLayerVisible(map, wantOn) {
    if (!map) return false;
    var input = findSuscOverlayCheckbox(map);
    if (input && Boolean(input.checked) !== Boolean(wantOn)) {
      try {
        input.click();
      } catch (eClick) { /* ignore */ }
    }
    if (input && Boolean(input.checked) === Boolean(wantOn)) {
      return true;
    }
    var layer = findSuscOverlayLayer(map);
    if (!layer) return false;
    try {
      if (wantOn && !map.hasLayer(layer)) map.addLayer(layer);
      if (!wantOn && map.hasLayer(layer)) map.removeLayer(layer);
      if (input && Boolean(input.checked) !== Boolean(wantOn)) {
        input.checked = !!wantOn;
      }
      return wantOn ? map.hasLayer(layer) : !map.hasLayer(layer);
    } catch (eSet) {
      return false;
    }
  }

  var _suscReloadInFlight = false;
  function reloadHomeMapForSusc() {
    if (_suscReloadInFlight) return;
    _suscReloadInFlight = true;
    window.setTimeout(function () { _suscReloadInFlight = false; }, 8000);
    var boot =
      document.getElementById("efc-stitch-home-boot") ||
      document.getElementById("portal-home-bootstrap");
    var panel = document.getElementById("home-map-panel");
    if (!panel || typeof htmx === "undefined" || !htmx.ajax) {
      _suscReloadInFlight = false;
      return;
    }
    var base =
      (boot && boot.getAttribute("data-map-url")) ||
      panel.getAttribute("hx-get") ||
      "/fragments/home/map/";
    base = String(base).replace(/&amp;/g, "&").replace(/[?&]_=[^&]*/g, "").replace(/\?$/, "");
    base = appendSuscPrefToUrl(base);
    if (boot) boot.setAttribute("data-map-url", base);
    panel.setAttribute("hx-get", base);
    var langPt = mapLangPt();
    panel.innerHTML =
      '<div class="efc-map-skel" aria-hidden="true"><div class="dj-skeleton dj-skeleton--map efc-map-skel__block"></div>' +
      '<p class="efc-map-skel__label">' +
      (langPt ? "Carregando suscetibilidade…" : "Loading susceptibility…") +
      "</p></div>";
    htmx.ajax("GET", upsertQueryParam(base, "_", String(Date.now())), {
      target: "#home-map-panel",
      swap: "innerHTML",
      source: panel,
      timeout: 180000,
    });
  }

  function suscLayerNeedsServerReload(layer) {
    if (!layer) return true;
    try {
      if (typeof layer.getLayers === "function" && layer.getLayers().length === 0) {
        // FeatureGroup vazio (placeholder leve) — precisa rebuild com raster/rail.
        return true;
      }
    } catch (e) { /* ignore */ }
    return false;
  }

  function bindSuscCheckboxPersist(map) {
    var input = findSuscOverlayCheckbox(map);
    if (!input || input._efcSuscChangeBound) return;
    input._efcSuscChangeBound = true;
    function syncFromInput() {
      var on = !!input.checked;
      writeSuscLayerPref(on);
      if (on && suscLayerNeedsServerReload(findSuscOverlayLayer(map))) {
        reloadHomeMapForSusc();
      }
    }
    input.addEventListener("change", syncFromInput);
    input.addEventListener("click", function () {
      window.setTimeout(syncFromInput, 0);
    });
  }

  function bindSuscLayerPersist(map) {
    if (!map || map._efcSuscPersistBound) return;
    map._efcSuscPersistBound = true;
    map.on("overlayadd", function (e) {
      if (!isSuscLayerName(e && e.name)) return;
      if (e.layer) map._efcSuscLayer = e.layer;
      writeSuscLayerPref(true);
      if (suscLayerNeedsServerReload(e.layer)) {
        reloadHomeMapForSusc();
      }
    });
    map.on("overlayremove", function (e) {
      if (!isSuscLayerName(e && e.name)) return;
      if (e.layer) map._efcSuscLayer = e.layer;
      writeSuscLayerPref(false);
    });
  }

  function observeSuscLayerControl(map) {
    var doc = suscMapDoc(map);
    if (!doc || typeof MutationObserver === "undefined") return;
    if (_suscLayerObserver && _suscLayerObserverDoc === doc) return;
    if (_suscLayerObserver) {
      try {
        _suscLayerObserver.disconnect();
      } catch (eObs) { /* ignore */ }
    }
    _suscLayerObserverDoc = doc;
    _suscLayerObserver = new MutationObserver(function () {
      if (findSuscOverlayCheckbox(map)) {
        scheduleSuscLayerRestore();
      }
    });
    try {
      var root =
        doc.querySelector(".leaflet-control-container") ||
        doc.querySelector(".folium-map") ||
        doc.body;
      if (root) {
        _suscLayerObserver.observe(root, { childList: true, subtree: true });
      }
    } catch (eObserve) { /* ignore */ }
  }

  /**
   * @returns {boolean} true when layer control is ready AND preference applied
   *   (or pref is OFF — checkbox present so user can still toggle).
   */
  function restoreSuscLayerAfterMapReady() {
    var mapFrame = document.getElementById("efc-map-frame") || document.getElementById("home-map-panel");
    var map = findLeafletMap(mapFrame || document);
    if (!map) return false;
    bindSuscLayerPersist(map);
    observeSuscLayerControl(map);
    var input = findSuscOverlayCheckbox(map);
    if (!input) return false;
    bindSuscCheckboxPersist(map);
    var wantOn = readSuscLayerPref();
    if (!wantOn) {
      // Pref OFF: checkbox must remain available; nothing to force-enable.
      return true;
    }
    return setSuscLayerVisible(map, true);
  }

  function scheduleSuscLayerRestore() {
    if (_suscLayerRestoreTimer) {
      clearTimeout(_suscLayerRestoreTimer);
      _suscLayerRestoreTimer = null;
    }
    var attempts = 0;
    function tryOnce() {
      attempts += 1;
      if (restoreSuscLayerAfterMapReady()) {
        if (_suscLayerObserver) {
          try {
            _suscLayerObserver.disconnect();
          } catch (eDisc) { /* ignore */ }
          _suscLayerObserver = null;
          _suscLayerObserverDoc = null;
        }
        return;
      }
      // Map embed can take several seconds (BQ + Folium); keep trying.
      // Also rely on iframe-local restore + ?susc= server show; this is backup.
      if (attempts < 120) {
        _suscLayerRestoreTimer = setTimeout(tryOnce, attempts < 20 ? 100 : 250);
      } else if (_suscLayerObserver) {
        try {
          _suscLayerObserver.disconnect();
        } catch (eGiveUp) { /* ignore */ }
        _suscLayerObserver = null;
        _suscLayerObserverDoc = null;
      }
    }
    _suscLayerRestoreTimer = setTimeout(tryOnce, 40);
  }

  /** Non-portal first paint: align iframe ?susc= with localStorage before Folium settles. */
  function ensureInitialMapIframeSuscPref() {
    var iframe = document.getElementById("efc-map-iframe");
    if (!iframe) return;
    var raw = iframe.getAttribute("src") || "";
    raw = String(raw).replace(/&amp;/g, "&");
    if (!raw || raw === "about:blank") return;
    var next = appendSuscPrefToUrl(raw);
    if (next === raw) return;
    iframe.setAttribute("data-embed-src", next);
    iframe.src = next;
  }

  function initSuscetibilidadeLayerPersist() {
    ensureInitialMapIframeSuscPref();
    scheduleSuscLayerRestore();
    if (_suscLayerPersistBound) return;
    _suscLayerPersistBound = true;
    window.addEventListener("message", function (ev) {
      if (!ev.data || ev.data.type !== "efc-map-ready") return;
      scheduleSuscLayerRestore();
    });
    document.body.addEventListener(
      "load",
      function (ev) {
        var t = ev.target;
        if (!t || !t.tagName || String(t.tagName).toLowerCase() !== "iframe") return;
        if (!t.classList || !t.classList.contains("efc-map-iframe")) {
          if (t.id !== "efc-map-iframe") return;
        }
        scheduleSuscLayerRestore();
      },
      true
    );
  }

  function initMapIframeTimeout() {
    var mapFrame = document.getElementById("efc-map-frame");
    if (!mapFrame) return;
    var iframe = mapFrame.querySelector("iframe.efc-map-iframe, iframe");
    if (!iframe || iframe.getAttribute("data-timeout-bound") === "1") return;
    iframe.setAttribute("data-timeout-bound", "1");
    var sec = parseInt(iframe.getAttribute("data-map-timeout-sec") || "0", 10);
    var timeoutMs = sec > 0 ? sec * 1000 : MAP_IFRAME_TIMEOUT_MS_DEFAULT;
    var langPt = mapLangPt();
    var done = false;
    function finishOk() {
      if (done) return;
      done = true;
      clearTimeout(timer);
      var overlay = document.getElementById("efc-map-skeleton-overlay");
      if (overlay) overlay.classList.add("is-hidden");
    }
    function finishFail() {
      if (done) return;
      done = true;
      clearTimeout(timer);
      showMapLoadError(mapFrame, langPt);
    }
    var timer = setTimeout(finishFail, timeoutMs);
    iframe.addEventListener("load", finishOk);
    try {
      var idoc = iframe.contentDocument;
      if (idoc && (idoc.querySelector(".folium-map") || idoc.querySelector("[id^='map_']"))) {
        finishOk();
      }
    } catch (e) {
      /* cross-origin or not ready */
    }
  }

  function initMapObservationCaption() {
    updateMapObservationCaption();
    if (mapCaptionBound) return;
    mapCaptionBound = true;
    var form =
      document.getElementById("efc-home-toolbar-form") ||
      document.querySelector(".efc-date-form");
    var controls = document.getElementById("efc-acc-alerts-controls");
    if (form) {
      form.querySelectorAll('input[name="accum"]').forEach(function (r) {
        r.addEventListener("change", updateMapObservationCaption);
      });
      var dateIn = form.querySelector('input[name="date"]') || document.getElementById("date");
      if (dateIn) dateIn.addEventListener("change", updateMapObservationCaption);
    }
    if (controls) {
      var toggle = controls.querySelector("#efc-acc-alerts-enabled");
      var modeSel = controls.querySelector("#efc-period-mode");
      var startIn = controls.querySelector("#efc-period-start");
      var endIn = controls.querySelector("#efc-period-end");
      if (toggle) toggle.addEventListener("change", updateMapObservationCaption);
      if (modeSel) modeSel.addEventListener("change", updateMapObservationCaption);
      if (startIn) startIn.addEventListener("change", updateMapObservationCaption);
      if (endIn) endIn.addEventListener("change", updateMapObservationCaption);
    }
  }

  function initAccumulatedAlerts() {
    var controls = document.getElementById("efc-acc-alerts-controls");
    if (!controls) return;
    if (accAlertsBoundControls === controls) {
      if (accAlertsMapApi && accAlertsMapApi.reattachMap) {
        accAlertsMapApi.reattachMap();
      }
      return;
    }
    accAlertsBoundControls = controls;

    applyPeriodFromUrl(controls);

    var toggle = controls.querySelector("#efc-acc-alerts-enabled");
    var modeSel = controls.querySelector("#efc-period-mode");
    var startIn = controls.querySelector("#efc-period-start");
    var endIn = controls.querySelector("#efc-period-end");
    var legend =
      document.getElementById("efc-map-bubble-legend") ||
      controls.querySelector("#efc-acc-alerts-legend");
    var statusEl = controls.querySelector("#efc-acc-alerts-status");

    function getMapFrame() {
      return document.getElementById("efc-map-frame");
    }

    function getLoadingEl() {
      var mapFrame = getMapFrame();
      return mapFrame ? mapFrame.querySelector("#efc-map-acc-loading") : null;
    }
    var apiBase = controls.getAttribute("data-api-url") || "/api/home/alertas-acumulados/";
    var langPt = (controls.getAttribute("data-lang") || "pt") === "pt";

    var layerGroup = null;
    var leafletMap = null;
    var fetchCtrl = null;
    var fetchSeq = 0;
    var cachedQuery = null;
    var cachedMarkers = null;
    var cachedStatusData = null;

    function periodQuery() {
      syncPeriodDaysHidden(controls);
      var st = readPeriodState(controls);
      var form =
        document.getElementById("efc-home-toolbar-form") ||
        document.querySelector(".efc-date-form");
      var dateVal = readToolbarDateIso(form);
      if (st.mode === "custom" && st.start && st.end) {
        return (
          "period_mode=custom&start=" +
          encodeURIComponent(st.start) +
          "&end=" +
          encodeURIComponent(st.end) +
          "&period=" +
          encodeURIComponent(st.periodVal)
        );
      }
      var days = st.days || String(accumHoursToDays(readToolbarAccumHours(form))) || "30";
      var q =
        "days=" +
        encodeURIComponent(days) +
        "&period=" +
        encodeURIComponent("days_" + days) +
        "&accum=" +
        encodeURIComponent(String(readToolbarAccumHours(form)));
      if (dateVal) {
        q +=
          "&date=" +
          encodeURIComponent(dateVal) +
          "&date_end=" +
          encodeURIComponent(dateVal);
      }
      return q;
    }

    function formatAccStatus(data, n) {
      var range =
        (data && data.period_range_pt) ||
        (data && data.period_label_pt) ||
        "";
      var civilDays =
        data &&
        (data.civil_days_with_alert != null
          ? data.civil_days_with_alert
          : data.distinct_alert_days);
      var parts = [
        (langPt ? "Exibindo " : "Showing ") + n + (langPt ? " trechos" : " segments"),
      ];
      if (civilDays != null && civilDays !== "") {
        parts.push(
          civilDays +
            (langPt ? " dias com alerta na ferrovia" : " days with alerts on the railway")
        );
      }
      if (range) parts.push(range);
      return parts.join(" · ") + ".";
    }

    function setStatus(msg, kind) {
      if (!statusEl) return;
      if (!msg) {
        statusEl.hidden = true;
        statusEl.textContent = "";
        statusEl.className = "efc-acc-alerts-status";
        return;
      }
      statusEl.hidden = false;
      statusEl.textContent = msg;
      statusEl.className = "efc-acc-alerts-status efc-acc-alerts-status--" + (kind || "info");
    }

    function popupHtml(m) {
      var n = m.n_tp != null ? m.n_tp : m.n_merge_24 != null ? m.n_merge_24 : m.n_alertas;
      return [
        "<b>km " + m.km + "</b>",
        (langPt ? "TP confirmados: " : "Confirmed TP: ") + n + (langPt ? " dia(s)" : " day(s)"),
      ].join("<br>");
    }

    function clearLayer() {
      if (layerGroup && leafletMap) {
        try {
          leafletMap.removeLayer(layerGroup);
        } catch (e) {
          /* ignore */
        }
      }
      layerGroup = null;
    }

    function getLeafletLib() {
      if (!leafletMap || !leafletMap._container) return null;
      try {
        return leafletMap._container.ownerDocument.defaultView.L;
      } catch (e) {
        return null;
      }
    }

    function mapIframeEl() {
      var mapFrame = getMapFrame();
      return mapFrame ? mapFrame.querySelector("iframe.efc-map-iframe, iframe") : null;
    }

    function applyMarkersViaPostMessage(markers) {
      var iframe = mapIframeEl();
      if (!iframe || !iframe.contentWindow) return false;
      try {
        iframe.contentWindow.postMessage(
          { type: "efc-acc-alerts", markers: markers || [] },
          window.location.origin
        );
        return true;
      } catch (e) {
        return false;
      }
    }

    function ensureAccAlertsPane(map) {
      if (!map || !map.createPane) return;
      if (!map.getPane("efcAccPane")) {
        map.createPane("efcAccPane");
        map.getPane("efcAccPane").style.zIndex = 450;
      }
    }

    function applyMarkers(markers) {
      if (mapIframeEl()) {
        return applyMarkersViaPostMessage(markers);
      }
      var L = getLeafletLib();
      if (L && leafletMap) {
        clearLayer();
        ensureAccAlertsPane(leafletMap);
        layerGroup = L.layerGroup();
        (markers || []).forEach(function (m) {
          var zoom = leafletMap.getZoom ? leafletMap.getZoom() : 8;
          var base = m.radius || 14;
          var extra = Math.max(0, zoom - 9);
          var radius = Math.min(52, Math.round(Math.max(12, base) + extra * 3.6));
          var marker = L.circleMarker([m.lat, m.lon], {
            radius: radius,
            color: "#0a0a0a",
            fillColor: m.color,
            fillOpacity: 0.8,
            weight: 2.6,
            opacity: 1,
            pane: "efcAccPane",
            zIndexOffset: 200,
          });
          marker._efcBaseR = base;
          marker
            .bindPopup(popupHtml(m))
            .bindTooltip("km " + m.km + " · " + m.n_alertas + (langPt ? " alertas Raijin" : " Raijin alerts"), {
              direction: "top",
              opacity: 0.9,
            })
            .addTo(layerGroup);
        });
        layerGroup.addTo(leafletMap);
        if (leafletMap && !leafletMap._efcAccZoomBound) {
          leafletMap._efcAccZoomBound = 1;
          leafletMap.on("zoomend", function () {
            if (!layerGroup) return;
            var z = leafletMap.getZoom();
            layerGroup.eachLayer(function (layer) {
              if (!layer.setRadius) return;
              var base = layer._efcBaseR || 14;
              var extra = Math.max(0, z - 9);
              layer.setRadius(Math.min(52, Math.round(Math.max(12, base) + extra * 3.6)));
            });
          });
        }
        return true;
      }
      return applyMarkersViaPostMessage(markers);
    }

    function setLoading(on) {
      var mapFrame = getMapFrame();
      var loadingEl = getLoadingEl();
      if (!loadingEl || !mapFrame) return;
      loadingEl.hidden = !on;
      mapFrame.classList.toggle("is-loading-acc", on);
    }

    function ensureMap() {
      if (leafletMap && leafletMap._container) return true;
      leafletMap = findLeafletMap(getMapFrame());
      return !!(leafletMap && leafletMap._container);
    }

    function fetchAndDraw() {
      if (!toggle || !toggle.checked) return;
      var q = periodQuery();
      if (cachedQuery === q && cachedMarkers) {
        ensureMap();
        if (applyMarkers(cachedMarkers)) {
          setStatus(
            cachedStatusData
              ? formatAccStatus(cachedStatusData, cachedMarkers.length)
              : null,
            cachedStatusData ? "ok" : null
          );
          return;
        }
      }
      if (fetchCtrl) fetchCtrl.abort();
      fetchCtrl = new AbortController();
      var reqSeq = ++fetchSeq;
      setLoading(true);
      setStatus(langPt ? "Carregando alertas acumulados…" : "Loading accumulated alerts…", "info");
      fetch(apiBase + "?" + q, { credentials: "same-origin", signal: fetchCtrl.signal })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          if (reqSeq !== fetchSeq) return;
          if (periodQuery() !== q) return;
          if (data.error) {
            throw new Error(data.error);
          }
          cachedQuery = q;
          cachedMarkers = data.markers || [];
          cachedStatusData = data;
          if (legend && legend.id === "efc-acc-alerts-legend") {
            legend.hidden = cachedMarkers.length === 0;
          }
          if (!cachedMarkers.length) {
            setLoading(false);
            setStatus(
              langPt
                ? "Nenhum alerta acumulado no período selecionado."
                : "No accumulated alerts in the selected period.",
              "warn"
            );
            clearLayer();
            return;
          }
          var tryApply = function (n) {
            if (reqSeq !== fetchSeq) return;
            if (ensureMap() && applyMarkers(cachedMarkers)) {
              setLoading(false);
              setStatus(formatAccStatus(data, cachedMarkers.length), "ok");
              return;
            }
            if (mapIframeEl() && applyMarkersViaPostMessage(cachedMarkers)) {
              setLoading(false);
              setStatus(formatAccStatus(data, cachedMarkers.length), "ok");
              return;
            }
            if (n < 60) {
              setTimeout(function () {
                tryApply(n + 1);
              }, 150);
            } else {
              setLoading(false);
              setStatus(
                langPt
                  ? "Mapa ainda não carregou — aguarde o painel ou recarregue a página."
                  : "Map not ready yet — wait for the panel or reload the page.",
                "error"
              );
              console.warn("Leaflet map not found for accumulated alerts overlay");
            }
          };
          tryApply(0);
        })
        .catch(function (err) {
          if (err.name === "AbortError") return;
          setLoading(false);
          setStatus(
            (langPt ? "Falha ao carregar alertas: " : "Failed to load alerts: ") + (err.message || err),
            "error"
          );
          console.warn("Accumulated alerts fetch failed", err);
        });
    }

    function onToggleChange() {
      updateMapObservationCaption();
      if (!toggle.checked) {
        clearLayer();
        applyMarkersViaPostMessage([]);
        if (legend) legend.hidden = true;
        setLoading(false);
        setStatus(null);
        if (fetchCtrl) fetchCtrl.abort();
        return;
      }
      fetchAndDraw();
    }

    if (toggle) toggle.addEventListener("change", onToggleChange);
    function reloadIndicatorsForPeriod() {
      var indicatorsUrl = controls.getAttribute("data-indicators-url");
      var indEl = document.getElementById("home-indicators");
      if (!indicatorsUrl || !indEl) return;
      var dateIn = document.getElementById("date");
      var dateVal = dateIn && dateIn.value ? dateIn.value : "";
      var q = periodQuery();
      var url =
        indicatorsUrl +
        (indicatorsUrl.indexOf("?") >= 0 ? "&" : "?") +
        (dateVal ? "date=" + encodeURIComponent(dateVal) + "&" : "") +
        q;
      if (typeof htmx !== "undefined" && htmx.ajax) {
        htmx.ajax("GET", url, { target: "#home-indicators", swap: "innerHTML" });
      }
      document.body.dispatchEvent(new CustomEvent("efc-period-change"));
    }

    var scheduleFetchAndDraw = debounce(fetchAndDraw, 280);

    function onPeriodControlChange() {
      syncPeriodDaysHidden(controls);
      syncHomePeriodToUrl(controls);
      cachedQuery = null;
      cachedMarkers = null;
      cachedStatusData = null;
      updateMapObservationCaption();
      reloadIndicatorsForPeriod();
      if (toggle && toggle.checked) scheduleFetchAndDraw();
    }

    var schedulePeriodChange = debounce(onPeriodControlChange, 320);

    if (modeSel) {
      modeSel.addEventListener("change", function () {
        setCustomPeriodVisible(controls, modeSel.value === "custom");
        syncPeriodDaysHidden(controls);
        schedulePeriodChange();
      });
    }
    if (startIn) startIn.addEventListener("change", schedulePeriodChange);
    if (endIn) endIn.addEventListener("change", schedulePeriodChange);

    function onMapIframeReady() {
      leafletMap = null;
      var overlay = document.getElementById("efc-map-skeleton-overlay");
      if (overlay) overlay.classList.add("is-hidden");
      if (toggle && toggle.checked) scheduleFetchAndDraw();
    }

    function onMapReadyMessage(ev) {
      var iframe = mapIframeEl();
      if (!iframe || ev.source !== iframe.contentWindow) return;
      if (!ev.data || ev.data.type !== "efc-map-ready") return;
      onMapIframeReady();
    }

    window.addEventListener("message", onMapReadyMessage);

    function bindMapIframeListeners() {
      var iframe = mapIframeEl();
      if (!iframe) return false;
      if (iframe.getAttribute("data-acc-alerts-bound") !== "1") {
        iframe.setAttribute("data-acc-alerts-bound", "1");
        iframe.addEventListener("load", onMapIframeReady);
      }
      try {
        var idoc = iframe.contentDocument;
        if (idoc && (idoc.querySelector(".folium-map") || idoc.querySelector("[id^='map_']"))) {
          onMapIframeReady();
          return true;
        }
      } catch (e) {
        /* not loaded yet */
      }
      return false;
    }

    function reattachMap() {
      leafletMap = null;
      layerGroup = null;
      bindMapIframeListeners();
      if (toggle && toggle.checked) {
        scheduleFetchAndDraw();
      } else if (mapIframeEl()) {
        applyMarkersViaPostMessage([]);
      }
    }

    function invalidateAccAlertsCache() {
      cachedQuery = null;
      cachedMarkers = null;
      cachedStatusData = null;
      leafletMap = null;
    }

    accAlertsMapApi = {
      reattachMap: reattachMap,
      invalidateCache: invalidateAccAlertsCache,
      redraw: scheduleFetchAndDraw,
    };

    var dateForm = document.querySelector(".efc-date-form");
    if (dateForm && dateForm.getAttribute("data-acc-alerts-date-bound") !== "1") {
      dateForm.setAttribute("data-acc-alerts-date-bound", "1");
      dateForm.addEventListener("submit", function () {
        invalidateAccAlertsCache();
      });
    }

    bindMapIframeListeners();
    if (!getMapFrame() && toggle && toggle.checked && !isPortalGraphiteHome()) {
      scheduleFetchAndDraw();
    }
  }

  function renderPlotsWithRetry(root, attempt) {
    attempt = attempt || 0;
    var scope = root || document;
    var pending = scope.querySelectorAll(".efc-plot:not([data-plot-rendered])");
    var hasScript = !!scope.querySelector("script.efc-plot-data");
    var hasAttr = !!scope.querySelector("[data-plot]");
    if (!pending.length && !hasScript && !hasAttr) {
      if (document.getElementById("efc-consultar-chat")) {
        scheduleConsultarChatScroll();
      }
      return;
    }
    ensurePlotlyLoaded()
      .then(function () {
        scope.querySelectorAll(".efc-previsao-heatmap-plot:not([data-plot-rendered])").forEach(function (el) {
          if (el._efcHeatmapFig) renderOneChart(el, el._efcHeatmapFig);
        });
        renderPlots(root);
        pending = scope.querySelectorAll(".efc-plot:not([data-plot-rendered])");
        if (!pending.length || attempt >= 30) {
          if (document.getElementById("efc-consultar-chat")) {
            scheduleConsultarChatScroll();
          }
          return;
        }
        var needsPlotly = !plotlyReady();
        var hasPlotPayload = false;
        pending.forEach(function (el) {
          if (el.getAttribute("data-plot")) hasPlotPayload = true;
          if (scope.querySelector("script.efc-plot-data[data-for='" + el.id + "']")) {
            hasPlotPayload = true;
          }
        });
        if (!hasPlotPayload && !needsPlotly) return;
        window.setTimeout(function () {
          renderPlotsWithRetry(root, attempt + 1);
        }, needsPlotly ? 120 : 80);
      })
      .catch(function () {
        /* CDN offline — páginas sem gráfico seguem ok */
      });
  }

  function buildChartsModalDom(payload) {
    var body = document.getElementById("efc-charts-modal-body");
    if (!body) return;
    var charts = (payload && payload.charts) || {};
    var html = "";
    if (payload && payload.error && !payload.available) {
      html += '<p class="efc-alert efc-alert--warn">' + payload.error + "</p>";
    }
    if (!payload || !payload.available) {
      html +=
        '<p class="efc-muted">Sem dados de gráficos para esta data. Verifique BigQuery e previsões recentes.</p>';
      body.innerHTML = html;
      return;
    }
    if (charts.heatmap) {
      html += '<section class="efc-card"><h3>Chuva 24 h prevista e concordância entre modelos</h3>';
      html += '<div id="chart-heatmap" class="efc-plot efc-plot--tall"></div></section>';
    }
    html += '<section class="efc-card"><h3>Por quilômetro</h3>';
    html += '<p class="efc-caption">km ' + payload.fc_km + " · " + payload.selected_date + "</p>";
    if (charts.km) {
      html += '<div id="chart-km-alert" class="efc-plot"></div>';
    }
    if (charts.forecast) {
      html += '<h4>Previsão 15 dias</h4><div id="chart-forecast" class="efc-plot"></div>';
    }
    if (!charts.km && !charts.forecast) {
      html += '<p class="efc-muted">Sem série temporal para este km/data.</p>';
    }
    html += "</section>";
    body.innerHTML = html;
    if (charts.heatmap) renderOneChart(document.getElementById("chart-heatmap"), charts.heatmap);
    if (charts.km) renderOneChart(document.getElementById("chart-km-alert"), charts.km);
    if (charts.forecast) renderOneChart(document.getElementById("chart-forecast"), charts.forecast);
    renderPlotsWithRetry(body, 0);
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function previsaoVerificationMetricVal(v) {
    if (v == null || v === "") return "—";
    return String(v);
  }

  function previsaoParseMetricNum(v) {
    if (v == null || v === "") return NaN;
    var s = String(v).replace("%", "").replace(",", ".").trim();
    return parseFloat(s);
  }

  function previsaoBestMetricKeys(models) {
    var rows = models || [];
    var best = { roc: null, pod: null, far: null, pbias: null };
    rows.forEach(function (m, idx) {
      var roc = previsaoParseMetricNum(m.roc_auc);
      var pod = previsaoParseMetricNum(m.pod);
      var far = previsaoParseMetricNum(m.far);
      var pb = previsaoParseMetricNum(
        m.pbias_display != null ? m.pbias_display : m.pbias
      );
      if (!isNaN(roc) && (best.roc === null || roc > best.roc.val)) {
        best.roc = { idx: idx, val: roc };
      }
      if (!isNaN(pod) && (best.pod === null || pod > best.pod.val)) {
        best.pod = { idx: idx, val: pod };
      }
      if (!isNaN(far) && (best.far === null || far < best.far.val)) {
        best.far = { idx: idx, val: far };
      }
      if (!isNaN(pb) && (best.pbias === null || Math.abs(pb) < Math.abs(best.pbias.val))) {
        best.pbias = { idx: idx, val: pb };
      }
    });
    return best;
  }

  function previsaoVerificationInsight(models) {
    var langPt = mapLangPt();
    var rows = models || [];
    if (!rows.length) return "";
    var best = previsaoBestMetricKeys(rows);
    var parts = [];
    if (best.roc != null) {
      parts.push(
        (langPt ? "Melhor ROC: " : "Best ROC: ") +
          rows[best.roc.idx].label +
          " (" +
          previsaoVerificationMetricVal(rows[best.roc.idx].roc_auc) +
          ")"
      );
    }
    if (best.pod != null && best.pod.idx !== (best.roc && best.roc.idx)) {
      parts.push(
        (langPt ? "maior POD: " : "highest POD: ") +
          rows[best.pod.idx].label +
          " (" +
          previsaoVerificationMetricVal(rows[best.pod.idx].pod) +
          ")"
      );
    }
    return parts.join(langPt ? " · " : " · ");
  }

  function previsaoVerificationTableHtml(models, help, pairsLabel) {
    var langPt = mapLangPt();
    var rows = models || [];
    if (!rows.length) return "";
    help = help || {};
    pairsLabel = pairsLabel || "n";
    var best = previsaoBestMetricKeys(rows);
    var html =
      '<div class="efc-pv-table-wrap"><table class="efc-pv-table"><thead><tr>' +
      "<th>" +
      (langPt ? "Modelo" : "Model") +
      "</th>" +
      '<th title="' +
      escapeHtml(help.roc_auc || "") +
      '">ROC</th>' +
      '<th title="' +
      escapeHtml(help.pod || "") +
      '">POD</th>' +
      '<th title="' +
      escapeHtml(help.far || "") +
      '">FAR</th>' +
      '<th title="' +
      escapeHtml(help.pbias || "") +
      '">PBIAS</th>' +
      "<th>" +
      escapeHtml(pairsLabel) +
      "</th></tr></thead><tbody>";
    rows.forEach(function (m, idx) {
      var src = (m.source || "").toLowerCase();
      function cell(cls, val) {
        var bestCls = cls && best[cls] && best[cls].idx === idx ? " efc-pv-table__cell--best" : "";
        return '<td class="efc-pv-table__cell' + bestCls + '">' + val + "</td>";
      }
      html +=
        '<tr class="efc-pv-table__row efc-pv-table__row--' +
        escapeHtml(src) +
        '"><th scope="row">' +
        escapeHtml(m.label || src.toUpperCase()) +
        "</th>" +
        cell("roc", previsaoVerificationMetricVal(m.roc_auc)) +
        cell("pod", previsaoVerificationMetricVal(m.pod)) +
        cell("far", previsaoVerificationMetricVal(m.far)) +
        cell(
          "pbias",
          previsaoVerificationMetricVal(
            m.pbias_display != null ? m.pbias_display : m.pbias
          )
        ) +
        '<td class="efc-pv-table__cell efc-pv-table__cell--n">' +
        escapeHtml(String(m.n_pairs || 0)) +
        "</td></tr>";
    });
    html += "</tbody></table></div>";
    return html;
  }

  function previsaoVerificationMetricCardsHtml(models, help, pairsLabel) {
    var html = "";
    var metricVal = previsaoVerificationMetricVal;
    help = help || {};
    pairsLabel = pairsLabel || "pares km·dia";
    (models || []).forEach(function (m) {
      var src = (m.source || "").toLowerCase();
      html +=
        '<article class="efc-previsao-metric-card efc-previsao-metric-card--' +
        escapeHtml(src) +
        '">';
      html +=
        '<h3 class="efc-previsao-metric-card__title">' +
        escapeHtml(m.label || src.toUpperCase()) +
        "</h3>";
      html +=
        '<p class="efc-previsao-metric-card__sub">' +
        escapeHtml(String(m.n_pairs || 0)) +
        " " +
        escapeHtml(pairsLabel) +
        "</p>";
      html += '<div class="efc-previsao-metric-card__vals">';
      html +=
        '<div class="efc-previsao-metric-card__val"><label title="' +
        escapeHtml(help.roc_auc || "") +
        '">ROC AUC</label><span>' +
        metricVal(m.roc_auc) +
        "</span></div>";
      html +=
        '<div class="efc-previsao-metric-card__val"><label title="' +
        escapeHtml(help.pbias || "") +
        '">PBIAS</label><span>' +
        metricVal(m.pbias_display != null ? m.pbias_display : m.pbias) +
        "</span></div>";
      html +=
        '<div class="efc-previsao-metric-card__val"><label title="' +
        escapeHtml(help.far || "") +
        '">FAR</label><span>' +
        metricVal(m.far) +
        "</span></div>";
      html +=
        '<div class="efc-previsao-metric-card__val"><label title="' +
        escapeHtml(help.pod || "") +
        '">POD</label><span>' +
        metricVal(m.pod) +
        "</span></div>";
      html += "</div>";
      if (help.roc_auc) {
        html +=
          '<p class="efc-previsao-metric-card__help">' +
          escapeHtml(help.roc_auc) +
          "</p>";
      }
      html += "</article>";
    });
    return html;
  }

  function previsaoVerificationMetaLine(verification) {
    if (!verification) return "";
    var obsLabel = verification.obs_label || "MERGE";
    var thr =
      verification.threshold_mm != null && verification.threshold_mm !== ""
        ? verification.threshold_mm
        : 5;
    var range =
      verification.verify_date_start && verification.verify_date_end
        ? verification.verify_date_start + " – " + verification.verify_date_end
        : "";
    var step = verification.scope_km_step;
    var parts = [obsLabel];
    if (range) parts.push(range);
    parts.push("≥ " + thr + " mm");
    if (step) parts.push(String(step) + " km");
    return parts.join(" · ");
  }

  function previsaoUpdateMetricsGlossary(help) {
    var glossary = document.getElementById("efc-previsao-metrics-glossary");
    var body = document.getElementById("efc-previsao-metrics-glossary-body");
    if (!glossary || !body || !help) return;
    help = help || {};
    var html = "";
    if (help.roc_auc) html += "<dt>ROC</dt><dd>" + escapeHtml(help.roc_auc) + "</dd>";
    if (help.pod) html += "<dt>POD</dt><dd>" + escapeHtml(help.pod) + "</dd>";
    if (help.far) html += "<dt>FAR</dt><dd>" + escapeHtml(help.far) + "</dd>";
    if (help.pbias) html += "<dt>PBIAS</dt><dd>" + escapeHtml(help.pbias) + "</dd>";
    if (!html) {
      glossary.hidden = true;
      return;
    }
    body.innerHTML = html;
    glossary.hidden = false;
  }

  function previsaoSetMetricsGridMode(dual) {
    var grid = document.getElementById("efc-previsao-metrics-grid");
    if (!grid) return;
    grid.classList.toggle("efc-pv-metrics-grid--dual", !!dual);
  }

  function buildPrevisaoVerificationDom(verification) {
    var host = document.getElementById("efc-previsao-verify");
    var metaEl = document.getElementById("efc-previsao-verify-meta");
    var insightEl = document.getElementById("efc-previsao-verify-insight");
    var verifyHint = document.getElementById("efc-previsao-verify-hint");
    if (!host) return;

    if (!verification || !verification.available || !verification.models || !verification.models.length) {
      var emptyMsg =
        (verification && verification.error) ||
        (verification && verification.empty_reason) ||
        "Sem dias com observação e previsão suficientes para calcular ROC AUC, PBIAS, FAR e POD na ferrovia nesta janela.";
      if (verification && verification.precompute_hint) {
        emptyMsg = verification.precompute_hint;
      }
      if (metaEl) metaEl.textContent = "";
      if (insightEl) {
        insightEl.hidden = true;
        insightEl.textContent = "";
      }
      host.innerHTML = '<p class="efc-muted">' + escapeHtml(emptyMsg) + "</p>";
      previsaoUpdateMetricsGlossary((verification && verification.metric_help) || {});
      previsaoSetMetricsGridMode(false);
      if (verifyHint) verifyHint.hidden = true;
      return;
    }

    var help = verification.metric_help || {};
    if (metaEl) metaEl.textContent = previsaoVerificationMetaLine(verification);
    var insight = previsaoVerificationInsight(verification.models);
    if (insightEl) {
      if (insight) {
        insightEl.textContent = insight;
        insightEl.hidden = false;
      } else {
        insightEl.hidden = true;
        insightEl.textContent = "";
      }
    }
    host.innerHTML = previsaoVerificationTableHtml(verification.models, help, "n");
    previsaoUpdateMetricsGlossary(help);
    previsaoSetMetricsGridMode(
      !document.getElementById("efc-previsao-km-verify-wrap") ||
        !document.getElementById("efc-previsao-km-verify-wrap").hidden
    );
    if (verifyHint) verifyHint.hidden = true;
  }

  function previsaoRangeCaption(payload) {
    if (!payload) return "";
    if (payload.chart_date_start && payload.chart_date_end) {
      return payload.chart_date_start + " – " + payload.chart_date_end;
    }
    return "";
  }

  function buildPrevisaoKmVerificationDom(verification, payload) {
    var langPt = mapLangPt();
    var host = document.getElementById("efc-previsao-km-verify");
    var panel = document.getElementById("efc-previsao-km-verify-wrap");
    var label = document.getElementById("efc-previsao-km-verify-label");
    if (!host || !verification) return;
    if (!verification.available || !verification.models || !verification.models.length) {
      host.innerHTML = "";
      if (panel) panel.hidden = true;
      previsaoSetMetricsGridMode(false);
      return;
    }
    var help = verification.metric_help || {};
    var km =
      payload && payload.fc_km != null
        ? payload.fc_km
        : (document.getElementById("km") && document.getElementById("km").value) || "";
    if (label) {
      label.textContent = langPt ? "Km " + km : "Km " + km;
    }
    host.innerHTML = previsaoVerificationTableHtml(verification.models, help, "n");
    if (panel) panel.hidden = false;
    previsaoSetMetricsGridMode(true);
    previsaoUpdateMetricsGlossary(help);
  }

  function buildPrevisaoHeatmapDom(payload) {
    var host = document.getElementById("efc-previsao-heatmap");
    var hint = document.getElementById("efc-previsao-heatmap-hint");
    var railHost = document.getElementById("efc-previsao-railway-chart");
    var railHint = document.getElementById("efc-previsao-rail-hint");
    if (!host) return;

    var charts = (payload && payload.charts) || {};
    var meta = (payload && payload.meta && payload.meta.heatmap) || {};
    var html = "";

    if (payload && payload.error && !charts.heatmap) {
      html +=
        '<p class="efc-alert efc-alert--warn" role="alert">' +
        escapeHtml(payload.error) +
        "</p>";
    } else if (!charts.heatmap) {
      html = '<p class="efc-muted">Sem heatmap.</p>';
    } else {
      var dotThr =
        meta.rain_dot_threshold_mm != null ? Math.round(Number(meta.rain_dot_threshold_mm)) : 1;
      if (!isFinite(dotThr) || dotThr < 0) dotThr = 1;
      html +=
        '<p class="efc-previsao-heatmap-legend efc-muted">○ MERGE observado (≥ ' +
        dotThr +
        " mm)</p>";
      html +=
        '<div id="chart-previsao-heatmap-auto" class="efc-plot efc-plot--tall efc-previsao-heatmap-plot"></div>';
    }

    host.innerHTML = html;
    host.hidden = false;
    if (hint) hint.hidden = true;

    if (charts.heatmap) {
      var heatEl = document.getElementById("chart-previsao-heatmap-auto");
      if (heatEl) heatEl._efcHeatmapFig = charts.heatmap;
      renderOneChart(heatEl, charts.heatmap);
      renderPlotsWithRetry(host, 0);
    }

    if (railHost) {
      if (charts.railway_models) {
        railHost.hidden = false;
        if (railHint) railHint.hidden = true;
        var railPlot = document.getElementById("chart-previsao-railway-models");
        renderOneChart(railPlot, charts.railway_models);
        if (railPlot && window.Plotly) {
          try {
            window.Plotly.relayout(railPlot, { height: 96, autosize: false });
          } catch (e) {
            /* ignore */
          }
        }
        renderPlotsWithRetry(railHost, 0);
      } else {
        railHost.hidden = true;
        if (railHint) {
          railHint.hidden = false;
          railHint.textContent = "Sem dados de excedência.";
        }
      }
    }
  }

  function buildPrevisaoChartsDom(payload) {
    buildPrevisaoDom(payload, { skipHeatmap: true });
  }

  function buildPrevisaoDom(payload, opts) {
    opts = opts || {};
    var wrap = document.getElementById("efc-previsao-charts");
    var chartsHint = document.getElementById("efc-previsao-charts-hint");
    if (!wrap) return;

    var charts = (payload && payload.charts) || {};
    var html = "";

    if (payload && payload.error) {
      html +=
        '<p class="efc-alert efc-alert--warn" role="alert">' +
        escapeHtml(payload.error) +
        "</p>";
    }

    if (!payload || !payload.available) {
      if (!html) {
        html =
          '<p class="efc-muted">Sem dados de previsão para estes parâmetros. Verifique BigQuery e ingestão recente.</p>';
      }
      wrap.innerHTML = html;
      wrap.hidden = false;
      if (chartsHint) chartsHint.hidden = true;
      if (payload && payload.verification) {
        buildPrevisaoKmVerificationDom(payload.verification, payload);
      }
      return;
    }

    if (charts.heatmap && !opts.skipHeatmap) {
      var meta = (payload.meta && payload.meta.heatmap) || {};
      html += '<section class="efc-card efc-previsao-section">';
      html +=
        "<h2>Chuva 24 h prevista (mediana) e concordância de faixa entre modelos</h2>";
      if (meta.caption) {
        html += '<p class="efc-caption">' + escapeHtml(meta.caption) + "</p>";
      }
      if (meta.faixas) {
        html +=
          '<p class="efc-previsao-faixas efc-muted">' + escapeHtml(meta.faixas) + "</p>";
      }
      html +=
        '<div id="chart-previsao-heatmap" class="efc-plot efc-plot--tall"></div></section>';
    }

    if (charts.forecast) {
      var kmVal =
        (payload && payload.fc_km != null
          ? payload.fc_km
          : document.getElementById("km") && document.getElementById("km").value) || "";
      var rangeCap = previsaoRangeCaption(payload);
      html +=
        '<div class="efc-previsao-chart-block efc-previsao-chart-block--forecast-only">' +
        '<p class="efc-previsao-chart-block__title">' +
        (mapLangPt() ? "Previsão no km " : "Forecast at km ") +
        escapeHtml(String(kmVal)) +
        (rangeCap ? " <span class=\"efc-previsao-chart-block__range\">(" + escapeHtml(rangeCap) + ")</span>" : "") +
        '</p><div id="chart-previsao-forecast" class="efc-plot efc-plot--forecast"></div></div>';
    } else {
      html += "<p class=\"efc-muted\">Sem série para este km.</p>";
    }

    wrap.innerHTML = html;
    wrap.hidden = false;
    if (chartsHint) chartsHint.hidden = true;

    if (charts.heatmap && !opts.skipHeatmap) {
      renderOneChart(document.getElementById("chart-previsao-heatmap"), charts.heatmap);
    }
    if (charts.forecast) {
      renderOneChart(
        document.getElementById("chart-previsao-forecast"),
        stripPrevisaoChartTitle(charts.forecast)
      );
    }
    renderPlotsWithRetry(wrap, 0);

    if (payload && payload.verification) {
      buildPrevisaoKmVerificationDom(payload.verification, payload);
    }
  }

  var PREVISAO_FETCH_MS = 120000;

  function fetchJsonWithTimeout(url, ctrl, ms) {
    var opts = { credentials: "same-origin", signal: ctrl.signal };
    var timeoutMs = ms || PREVISAO_FETCH_MS;
    var timedOut = false;
    var timer = setTimeout(function () {
      timedOut = true;
      try {
        ctrl.abort();
      } catch (e) {
        /* ignore */
      }
    }, timeoutMs);
    return fetch(url, opts)
      .then(function (r) {
        clearTimeout(timer);
        if (r.status === 401) {
          return r
            .json()
            .catch(function () {
              return null;
            })
            .then(function (body) {
              var loginUrl =
                (body && body.login_url) || monitorLoginRedirectUrl();
              if (!monitorAuthRedirecting) {
                monitorAuthRedirecting = true;
                window.location.replace(loginUrl);
              }
              throw new Error("HTTP 401");
            });
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .catch(function (err) {
        clearTimeout(timer);
        if (timedOut && err.name === "AbortError") {
          throw new Error(
            "tempo esgotado após " + Math.round(timeoutMs / 1000) + " s — tente um horizonte menor ou recarregue"
          );
        }
        throw err;
      });
  }

  function fillAtivoSegmentSelect(sel, segments, selectedId) {
    if (!sel || !segments || !segments.length) return;
    var prev = selectedId || sel.value || "";
    if (
      sel.options.length === segments.length &&
      sel.options.length > 0 &&
      sel.options[0].value === segments[0].segment_id
    ) {
      if (prev) sel.value = prev;
      return;
    }
    sel.innerHTML = "";
    segments.forEach(function (s) {
      var opt = document.createElement("option");
      opt.value = s.segment_id;
      var parts = [s.segment_id];
      if (s.km_label) parts.push("km " + s.km_label);
      if (s.segment_name && s.segment_name !== s.segment_id) parts.push(s.segment_name);
      opt.textContent = parts.join(" · ");
      opt.title = opt.textContent;
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
  }

  function stripPrevisaoChartTitle(fig) {
    if (!fig || !fig.layout) return fig;
    var out = JSON.parse(JSON.stringify(fig));
    out.layout.title = "";
    out.layout.margin = out.layout.margin || {};
    if (!out.layout.margin.t || out.layout.margin.t > 40) {
      out.layout.margin.t = 28;
    }
    return out;
  }

  function buildPrevisaoAtivosDom(payload) {
    var hint = document.getElementById("efc-previsao-ativos-hint");
    var chartWrap = document.getElementById("efc-previsao-ativos-charts");
    var metaEl = document.getElementById("efc-previsao-ativos-chart-meta");
    var sel = document.getElementById("ativo-segment");
    var langPt = mapLangPt();
    if (!sel) return;

    if (payload && payload.segments && payload.segments.length) {
      var selectedId =
        (payload.selected && payload.selected.segment_id) || sel.value || "";
      fillAtivoSegmentSelect(sel, payload.segments, selectedId);
    }

    if (!payload || !payload.available) {
      var msg =
        (payload && payload.error) ||
        (langPt ? "Sem ativos disponíveis." : "No assets available.");
      if (hint) {
        hint.hidden = false;
        hint.textContent = msg;
      }
      if (chartWrap) chartWrap.hidden = true;
      if (metaEl) metaEl.hidden = true;
      return;
    }

    if (hint) hint.hidden = true;

    var selected = payload.selected || {};
    if (metaEl) {
      var parts = [];
      if (selected.segment_id) parts.push(selected.segment_id);
      if (selected.km_label) parts.push("km " + selected.km_label);
      if (selected.segment_name && selected.segment_name !== selected.segment_id) {
        parts.push(selected.segment_name);
      }
      if (payload.chart_date_start && payload.chart_date_end) {
        parts.push(payload.chart_date_start + " – " + payload.chart_date_end);
      }
      metaEl.textContent = parts.join(" · ");
      metaEl.hidden = !parts.length;
    }

    var charts = (payload && payload.charts) || {};
    var forecast = charts.forecast || payload.chart;
    if (chartWrap && forecast) {
      chartWrap.hidden = false;
      renderOneChart(
        document.getElementById("chart-previsao-ativos"),
        stripPrevisaoChartTitle(forecast)
      );
      renderPlotsWithRetry(chartWrap, 0);
    } else if (chartWrap) {
      chartWrap.hidden = true;
      if (hint) {
        hint.hidden = false;
        hint.textContent = langPt
          ? "Sem série de previsão para este ativo."
          : "No forecast series for this asset.";
      }
    }
  }

  function initPrevisaoAtivos() {
    var form = document.getElementById("efc-previsao-ativos-form");
    if (!form) return;
    var langPt = mapLangPt();
    var ativosCtrl = null;
    var loadBtn = document.getElementById("efc-ativo-load");
    var hint = document.getElementById("efc-previsao-ativos-hint");
    var mainForm = document.getElementById("efc-previsao-form");
    var sel = document.getElementById("ativo-segment");

    function ativosUrl() {
      var base = form.getAttribute("data-ativos-url");
      if (!base) return null;
      try {
        var u = new URL(base, window.location.origin);
        if (sel && sel.value) u.searchParams.set("segment", sel.value);
        if (mainForm) {
          var past = mainForm.querySelector("#horizon_past");
          var future = mainForm.querySelector("#horizon_future");
          if (past && past.value !== "") u.searchParams.set("horizon_past", past.value);
          if (future && future.value !== "") u.searchParams.set("horizon_future", future.value);
        }
        return u.href;
      } catch (e) {
        return base;
      }
    }

    function fetchAtivos() {
      var url = ativosUrl();
      if (!url) return;
      if (ativosCtrl) ativosCtrl.abort();
      ativosCtrl = new AbortController();
      if (hint) {
        hint.hidden = false;
        hint.textContent = langPt ? "Carregando…" : "Loading…";
      }
      fetchJsonWithTimeout(url, ativosCtrl, PREVISAO_FETCH_MS)
        .then(buildPrevisaoAtivosDom)
        .catch(function (err) {
          if (err.name === "AbortError" && !err.message) return;
          if (hint) {
            hint.hidden = false;
            hint.textContent =
              (langPt ? "Não foi possível carregar ativos: " : "Could not load assets: ") +
              (err.message || err);
          }
          buildPrevisaoAtivosDom({ available: false, error: err.message || String(err) });
        });
    }

    var scheduleAtivos = debounce(fetchAtivos, 350);

    scheduleAtivos();

    if (loadBtn) loadBtn.addEventListener("click", fetchAtivos);
    if (sel) sel.addEventListener("change", fetchAtivos);

    if (mainForm) {
      mainForm.querySelectorAll("#horizon_past, #horizon_future").forEach(function (el) {
        el.addEventListener("change", scheduleAtivos);
      });
    }
  }

  function initPrevisaoPage() {
    var loadBtn = document.getElementById("efc-previsao-load");
    var wrap = document.getElementById("efc-previsao-charts");
    var chartsHint = document.getElementById("efc-previsao-charts-hint");
    var form = document.getElementById("efc-previsao-form");
    if (!form) return;
    var langPt = mapLangPt();

    var previsaoCtrl = null;
    var heatmapCtrl = null;
    var verifyWrap = document.getElementById("efc-previsao-verify-wrap");
    var verifyHint = document.getElementById("efc-previsao-verify-hint");

    function previsaoQueryUrl(base) {
      if (!base) return null;
      var pastIn = form.querySelector("#horizon_past");
      var futureIn = form.querySelector("#horizon_future");
      try {
        var u = new URL(base, window.location.origin);
        if (pastIn && pastIn.value !== "") u.searchParams.set("horizon_past", pastIn.value);
        if (futureIn && futureIn.value !== "") u.searchParams.set("horizon_future", futureIn.value);
        u.searchParams.set("obs", "merge");
        return u.href;
      } catch (e) {
        return base.indexOf("/") === 0 ? window.location.origin + base : base;
      }
    }

    function heatmapUrl() {
      return previsaoQueryUrl(form.getAttribute("data-heatmap-url"));
    }

    function verificationUrl() {
      return previsaoQueryUrl(form.getAttribute("data-verification-url"));
    }

    var verifyCtrl = null;

    function fetchVerificationAuto() {
      var verifyUrl = verificationUrl();
      if (!verifyUrl || !verifyWrap) return;
      if (verifyCtrl) verifyCtrl.abort();
      verifyCtrl = new AbortController();
      verifyWrap.hidden = false;
      if (verifyHint) {
        verifyHint.hidden = false;
        verifyHint.textContent = langPt ? "Carregando…" : "Loading…";
      }
      fetchJsonWithTimeout(verifyUrl, verifyCtrl, 60000)
        .then(function (payload) {
          if (payload) {
            buildPrevisaoVerificationDom(payload);
          } else if (verifyHint) {
            verifyHint.hidden = false;
            verifyHint.textContent = "Sem dados de verificação no cache.";
          }
        })
        .catch(function (err) {
          if (err.name === "AbortError" && !err.message) return;
          if (verifyHint) {
            verifyHint.hidden = false;
            verifyHint.textContent =
              "Não foi possível carregar a verificação: " + (err.message || err);
          }
          buildPrevisaoVerificationDom({ available: false, error: err.message || String(err) });
        });
    }

    function fetchHeatmapAuto() {
      var heatUrl = heatmapUrl();
      var heatHost = document.getElementById("efc-previsao-heatmap");
      if (!heatUrl || !heatHost) return;
      if (heatmapCtrl) heatmapCtrl.abort();
      heatmapCtrl = new AbortController();
      var heatHint = document.getElementById("efc-previsao-heatmap-hint");
      var railHint = document.getElementById("efc-previsao-rail-hint");
      var railHost = document.getElementById("efc-previsao-railway-chart");
      if (heatHint) {
        heatHint.hidden = false;
        heatHint.textContent = langPt ? "Carregando…" : "Loading…";
      }
      if (railHint) {
        railHint.hidden = false;
        railHint.textContent = langPt ? "Carregando…" : "Loading…";
      }
      if (railHost) railHost.hidden = true;
      heatHost.hidden = true;
      fetchJsonWithTimeout(heatUrl, heatmapCtrl, PREVISAO_FETCH_MS)
        .then(buildPrevisaoHeatmapDom)
        .catch(function (err) {
          if (err.name === "AbortError" && !err.message) return;
          var msg = err.message || String(err);
          if (heatHint) {
            heatHint.hidden = false;
            heatHint.textContent = "Não foi possível carregar o heatmap: " + msg;
          }
          heatHost.hidden = false;
          heatHost.innerHTML =
            '<p class="efc-alert efc-alert--warn" role="alert">' +
            escapeHtml(msg) +
            "</p>";
        });
    }

    var scheduleHeatmapAuto = debounce(fetchHeatmapAuto, 400);
    var scheduleChartsAuto = debounce(function () {
      if (wrap) fetchCharts();
    }, 500);

    scheduleHeatmapAuto();

    if (!loadBtn || !wrap) return;

    function chartsUrl() {
      var base = form.getAttribute("data-charts-url");
      if (!base) return null;
      var kmIn = form.querySelector("#km");
      var pastIn = form.querySelector("#horizon_past");
      var futureIn = form.querySelector("#horizon_future");
      try {
        var u = new URL(base, window.location.origin);
        if (kmIn && kmIn.value !== "") u.searchParams.set("km", kmIn.value);
        if (pastIn && pastIn.value !== "") u.searchParams.set("horizon_past", pastIn.value);
        if (futureIn && futureIn.value !== "") u.searchParams.set("horizon_future", futureIn.value);
        u.searchParams.set("obs", "merge");
        return u.href;
      } catch (e) {
        return base.indexOf("/") === 0 ? window.location.origin + base : base;
      }
    }

    function showPrevisaoError(msg) {
      wrap.innerHTML =
        '<p class="efc-alert efc-alert--warn" role="alert">' + escapeHtml(msg) + "</p>";
      wrap.hidden = false;
      if (chartsHint) chartsHint.hidden = true;
    }

    function setLoading() {
      wrap.hidden = false;
      wrap.innerHTML =
        '<div class="efc-charts-loading" role="status" aria-live="polite">' +
        '<div class="efc-charts-loading__spinner" aria-hidden="true"></div>' +
        '<p class="efc-charts-loading__msg">…</p></div>';
      var kmPanel = document.getElementById("efc-previsao-km-verify-wrap");
      var kmHost = document.getElementById("efc-previsao-km-verify");
      if (kmPanel) kmPanel.hidden = true;
      if (kmHost) kmHost.innerHTML = "";
      previsaoSetMetricsGridMode(false);
      if (chartsHint) {
        chartsHint.hidden = false;
        chartsHint.textContent = langPt ? "Carregando…" : "Loading…";
      }
    }

    function fetchCharts() {
      var url = chartsUrl();
      if (!url) {
        showPrevisaoError("URL da API não configurada.");
        return;
      }
      if (previsaoCtrl) previsaoCtrl.abort();
      previsaoCtrl = new AbortController();
      loadBtn.disabled = true;
      setLoading();

      fetchJsonWithTimeout(url, previsaoCtrl, PREVISAO_FETCH_MS)
        .then(buildPrevisaoChartsDom)
        .catch(function (err) {
          if (err.name === "AbortError") return;
          console.error("previsao charts", err);
          var msg = err.message || String(err);
          if (msg === "Failed to fetch") {
            msg =
              "tempo esgotado ou conexão interrompida — a consulta ao BigQuery pode levar até 90 s; tente novamente";
          }
          showPrevisaoError("Não foi possível carregar os gráficos: " + msg);
        })
        .finally(function () {
          loadBtn.disabled = false;
        });
    }

    loadBtn.addEventListener("click", fetchCharts);

    scheduleChartsAuto();

    if (form) {
      form.querySelectorAll("#horizon_past, #horizon_future, #km").forEach(function (el) {
        el.addEventListener("change", scheduleHeatmapAuto);
        el.addEventListener("change", scheduleChartsAuto);
      });
    }
  }

  function initChartsModal() {
    var modal = document.getElementById("efc-charts-modal");
    var openBtn = document.getElementById("efc-charts-open");
    var body = document.getElementById("efc-charts-modal-body");
    if (!modal || !openBtn || !body) return;

    var chartsCtrl = null;

    function closeModal() {
      modal.hidden = true;
      if (chartsCtrl) chartsCtrl.abort();
    }

    function openModal() {
      modal.hidden = false;
      setLoadPct(Math.max(loadPct, 75));
    }

    openBtn.addEventListener("click", function () {
      var url = openBtn.getAttribute("data-charts-url");
      if (!url) return;
      openModal();
      if (chartsCtrl) chartsCtrl.abort();
      chartsCtrl = new AbortController();
      body.innerHTML =
        '<div class="efc-charts-loading" role="status" aria-live="polite">' +
        '<div class="efc-charts-loading__spinner" aria-hidden="true"></div>' +
        '<p class="efc-charts-loading__msg">Carregando gráficos…</p>' +
        "</div>";
      ensurePlotlyLoaded()
        .catch(function () { /* still try fetch */ })
        .then(function () {
          return fetch(url, { credentials: "same-origin", signal: chartsCtrl.signal });
        })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (payload) {
          buildChartsModalDom(payload);
        })
        .catch(function (err) {
          if (err.name === "AbortError") return;
          body.innerHTML =
            '<p class="efc-alert efc-alert--warn">Não foi possível carregar os gráficos: ' +
            (err.message || err) +
            "</p>";
        });
    });

    modal.querySelectorAll("[data-close-modal]").forEach(function (el) {
      el.addEventListener("click", closeModal);
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !modal.hidden) closeModal();
    });
  }

  function onHtmxAfterSwap(ev) {
    var target = ev.detail && ev.detail.target;
    if (!target || !target.id) return;
    if (target.id === "home-day-strip") setLoadPct(35);
    if (target.id === "home-alerts-slot") setLoadPct(55);
    if (target.id === "home-exceed-slot") setLoadPct(62);
    if (target.id === "home-indicators") {
      setLoadPct(78);
      if (isPortalGraphiteHome()) {
        sessionStorage.removeItem("efc429_home-indicators");
        // Mapa sobe em paralelo no bootstrap; fallback se ainda não partiu.
        var bootInd = document.getElementById("portal-home-bootstrap");
        if (bootInd && bootInd.getAttribute("data-map-loading") !== "1") {
          loadPortalMapPanel(bootInd);
        }
      } else {
        scheduleStitchIndicatorsRetry();
      }
    }
    if (target.id === "home-map-panel") {
      setLoadPct(75);
      if (isPortalGraphiteHome()) {
        loadDeferredPortalMapIframe();
        sessionStorage.removeItem("efc429_home-map-panel");
        var bootMap = document.getElementById("portal-home-bootstrap");
        if (bootMap) bootMap.removeAttribute("data-map-loading");
      } else {
        // Só força reload do iframe quando a troca veio da toolbar (data/janela).
        if (target.getAttribute("data-refresh-gen")) {
          forceMapIframeReload(target);
          target.removeAttribute("data-refresh-gen");
        }
        initAccumulatedAlerts();
      }
      initMapObservationCaption();
      initMapIframeTimeout();
      initMapLegendCollapse();
      initMapLayerStrip();
      initSuscetibilidadeLayerPersist();
      if (accAlertsMapApi && accAlertsMapApi.invalidateCache) {
        accAlertsMapApi.invalidateCache();
      }
    }
    if (target.id === "efc-charts-modal-body") {
      setLoadPct(100);
    }
    var main = document.getElementById("efc-home-main");
    if (loadPct >= 75 && main) {
      main.setAttribute("aria-busy", "false");
      var bar = document.getElementById("efc-load-progress");
      if (bar) bar.classList.add("is-done");
    }
  }

  function formatBoletimDatePt(isoDate) {
    if (!isoDate) return "";
    var p = String(isoDate).slice(0, 10).split("-");
    if (p.length !== 3) return isoDate;
    return p[2] + "/" + p[1] + "/" + p[0];
  }

  function boletimDayFromClick(pt) {
    if (!pt) return null;
    var x = pt.x != null ? pt.x : pt.label;
    if (x == null) return null;
    var s = String(x);
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
    var d = new Date(x);
    if (!isNaN(d.getTime())) {
      return d.toISOString().slice(0, 10);
    }
    return null;
  }

  function isBoletimExceedanceClick(pt, threshold) {
    if (!pt) return false;
    var y = Number(pt.y);
    if (!isFinite(y) || y < threshold) return false;
    var name = String((pt.data && pt.data.name) || "");
    if (name.indexOf("Excedência") >= 0 || name.indexOf("≥") >= 0) return true;
    if (name.indexOf("Chuva 24 h") >= 0) return true;
    return false;
  }

  function renderBoletimDayChart(figJson) {
    var el = document.getElementById("chart-boletim-day");
    if (!el) return;
    el.removeAttribute("data-plot-rendered");
    var fig = typeof figJson === "string" ? parsePlotJson(figJson) : figJson;
    if (!fig) return;
    renderOneChart(el, fig);
  }

  function setBoletimOccurrenceChipActive(dateIso) {
    document.querySelectorAll(".efc-boletim-occ__chip[data-boletim-date]").forEach(function (btn) {
      var on = btn.getAttribute("data-boletim-date") === dateIso;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
  }

  function updateBoletimDayHeader(header, payload) {
    if (!header || !payload || !payload.subtitle) return;
    var title = payload.panel_title || "Ocorrência de um dia";
    header.innerHTML =
      '<div class="efc-panel-head"><h3>' +
      '<span aria-hidden="true" style="margin-right:0.35rem;">⏱️</span>' +
      escapeHtml(title) +
      "</h3><p>" +
      escapeHtml(payload.subtitle) +
      "</p></div>";
  }

  function initIngestionCountdown() {
    var el = document.getElementById("efc-next-ingestion");
    if (!el) return;
    var iso = el.getAttribute("data-next-run");
    if (!iso) return;
    var target = new Date(iso);
    if (isNaN(target.getTime())) return;

    var staticLabel = (el.textContent || "").trim();

    function pad2(n) {
      return n < 10 ? "0" + n : String(n);
    }

    function formatLocal(d) {
      return (
        pad2(d.getDate()) +
        "-" +
        pad2(d.getMonth() + 1) +
        "-" +
        d.getFullYear() +
        " " +
        pad2(d.getHours()) +
        ":" +
        pad2(d.getMinutes())
      );
    }

    function tick() {
      var ms = target.getTime() - Date.now();
      if (ms <= 0) {
        el.textContent = (staticLabel || formatLocal(target)) + " (em breve)";
        return;
      }
      var totalMin = Math.max(1, Math.ceil(ms / 60000));
      if (totalMin > 120) {
        el.textContent = staticLabel || formatLocal(target);
        return;
      }
      var h = Math.floor(totalMin / 60);
      var m = totalMin % 60;
      var parts = [];
      if (h > 0) parts.push(h + " h");
      if (m > 0 || !h) parts.push(m + " min");
      el.textContent = "faltam " + parts.join(" ") + " · " + (staticLabel || formatLocal(target));
    }

    tick();
    window.setInterval(tick, 60000);
  }

  function initBoletinsPage() {
    var yearEl = document.getElementById("chart-boletim-year");
    if (!yearEl || yearEl.getAttribute("data-boletim-bound") === "1") return;
    yearEl.setAttribute("data-boletim-bound", "1");

    var apiUrl = yearEl.getAttribute("data-boletim-api") || "/api/boletim/dia-distribuicao/";
    var kmStart = yearEl.getAttribute("data-boletim-km-start") || "";
    var kmEnd = yearEl.getAttribute("data-boletim-km-end") || "";
    var threshold = parseFloat(yearEl.getAttribute("data-boletim-threshold") || "49");
    var loading = document.getElementById("efc-boletim-day-loading");
    var hint = document.getElementById("efc-boletim-day-hint");
    var header = document.getElementById("efc-boletim-day-header");

    function loadBoletimDay(dateIso, opts) {
      opts = opts || {};
      if (!dateIso) return;
      setBoletimOccurrenceChipActive(dateIso);
      if (loading) loading.hidden = false;
      var q =
        "?date=" +
        encodeURIComponent(dateIso) +
        "&km_start=" +
        encodeURIComponent(kmStart) +
        "&km_end=" +
        encodeURIComponent(kmEnd);
      fetch(apiUrl + q, { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(function (payload) {
          if (loading) loading.hidden = true;
          if (!payload || !payload.available) {
            if (hint) {
              hint.textContent =
                (payload && payload.error) || "Sem distribuição sub-diária para este dia.";
            }
            return;
          }
          updateBoletimDayHeader(header, payload);
          if (hint) {
            hint.textContent =
              "Chuva 24 h no dia: " +
              formatBoletimDatePt(payload.date) +
              (payload.kind_label_pt ? " · " + payload.kind_label_pt : "");
          }
          renderBoletimDayChart(payload.chart);
          if (opts.scroll) {
            var panel = document.getElementById("efc-boletim-day-panel");
            if (panel && panel.scrollIntoView) {
              panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }
          }
        })
        .catch(function () {
          if (loading) loading.hidden = true;
          if (hint) hint.textContent = "Erro ao carregar distribuição do dia.";
        });
    }

    var chipsRoot = document.getElementById("efc-boletim-occ-chips");
    if (chipsRoot) {
      chipsRoot.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".efc-boletim-occ__chip[data-boletim-date]");
        if (!btn) return;
        loadBoletimDay(btn.getAttribute("data-boletim-date"), { scroll: true });
      });
    }

    function bindYearClick() {
      if (!yearEl.getAttribute("data-plot-rendered")) {
        setTimeout(bindYearClick, 150);
        return;
      }
      yearEl.on("plotly_click", function (ev) {
        var pt = ev && ev.points && ev.points[0];
        if (!isBoletimExceedanceClick(pt, threshold)) return;
        var dateIso = boletimDayFromClick(pt);
        if (!dateIso) return;
        loadBoletimDay(dateIso, { scroll: true });
      });
    }

    bindYearClick();
  }

  function initSidebarDrawer() {
    var layout = document.getElementById("efc-layout");
    var toggle = document.getElementById("efc-sidebar-toggle");
    var backdrop = document.getElementById("efc-sidebar-backdrop");
    var sidebar = document.getElementById("efc-sidebar");
    if (!layout || !toggle || !sidebar) return;

    function setOpen(open) {
      layout.classList.toggle("is-sidebar-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (backdrop) {
        backdrop.hidden = !open;
        backdrop.tabIndex = open ? 0 : -1;
      }
      document.body.style.overflow = open ? "hidden" : "";
    }

    toggle.addEventListener("click", function () {
      setOpen(!layout.classList.contains("is-sidebar-open"));
    });
    if (backdrop) {
      backdrop.addEventListener("click", function () {
        setOpen(false);
      });
    }
    sidebar.querySelectorAll(".efc-nav__link").forEach(function (link) {
      link.addEventListener("click", function () {
        if (window.matchMedia("(max-width: 900px)").matches) {
          setOpen(false);
        }
      });
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && layout.classList.contains("is-sidebar-open")) {
        setOpen(false);
      }
    });
  }

  function syncToolbarDateDisplay(form) {
    if (!form) return;
    var wrap = form.querySelector(".dj-date-picker");
    var dateIn = form.querySelector('input[name="date"]');
    var display = form.querySelector("#efc-toolbar-date-display");
    if (!wrap || !dateIn || !display) return;
    var iso = dateIn.value || "";
    if (iso) {
      display.textContent = formatDateBr(iso);
      wrap.setAttribute("data-date-display", formatDateBr(iso));
    } else if (wrap.getAttribute("data-date-display")) {
      display.textContent = wrap.getAttribute("data-date-display");
    }
  }

  function initRaijinBell() {
    var wrap = document.getElementById("efc-raijin-bell");
    var btn = document.getElementById("efc-raijin-bell-btn");
    var panel = document.getElementById("efc-raijin-bell-panel");
    var body = document.getElementById("efc-raijin-bell-body");
    var dot = document.getElementById("efc-raijin-bell-dot");
    if (!wrap || !btn || !panel || !body) return;

    var url = btn.getAttribute("data-raijin-url") || "";
    var loaded = false;
    var loading = false;
    var langPt = mapLangPt();

    function setOpen(open) {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
    }

    function fmtDay(iso) {
      if (!iso) return "—";
      var p = String(iso).slice(0, 10).split("-");
      if (p.length !== 3) return iso;
      return langPt ? p[2] + "/" + p[1] + "/" + p[0] : p[1] + "/" + p[2] + "/" + p[0];
    }

    function outcomeBadge(oc, kind) {
      var label = String(oc || "pending").toUpperCase();
      if (oc === "ews" || oc === "limiar" || kind === "threshold") {
        label = oc === "limiar" ? "LIMIAR" : "EWS";
      }
      var cls = "efc-raijin-bell__badge";
      if (oc === "tp") cls += " efc-raijin-bell__badge--tp";
      else if (oc === "fn") cls += " efc-raijin-bell__badge--fn";
      else if (oc === "fp") cls += " efc-raijin-bell__badge--fp";
      else if (oc === "ews" || oc === "limiar" || kind === "threshold") cls += " efc-raijin-bell__badge--ews";
      return '<span class="' + cls + '">' + label + "</span>";
    }

    function render(data) {
      if (dot) {
        if (data && data.has_unread) dot.removeAttribute("hidden");
        else dot.setAttribute("hidden", "");
      }
      var alerts = (data && data.alerts) || [];
      var status = (data && data.ews_status) || null;
      var statusHtml = "";
      if (status && status.ok && status.max_mm) {
        var maxDay = status.max_mm.day != null ? status.max_mm.day : "—";
        var max7 = status.max_mm["7d"] != null ? status.max_mm["7d"] : "—";
        var n24 = (status.n_above && status.n_above["24h"]) || 0;
        statusHtml =
          '<p class="efc-raijin-bell__status">' +
          (langPt
            ? "EWS agora: máx dia " + maxDay + " mm · 7d " + max7 + " mm · n_above 24h=" + n24
            : "EWS now: max day " + maxDay + " mm · 7d " + max7 + " mm · n_above 24h=" + n24) +
          "</p>";
      }
      if (!alerts.length) {
        body.innerHTML =
          statusHtml +
          '<p class="efc-muted" style="font-size:0.75rem;margin:0.65rem 0.9rem">' +
          ((data && data.empty) ||
            (langPt
              ? "Nenhum alerta Raijin nem limiar EWS no período."
              : "No Raijin or EWS threshold alerts in the period.")) +
          "</p>";
        return;
      }
      body.innerHTML =
        statusHtml +
        alerts
          .map(function (a) {
            var mm = a.mm != null ? Math.round(Number(a.mm)) + " mm" : "—";
            var extra = a.limiar ? " · " + a.limiar : "";
            return (
              '<div class="efc-raijin-bell__item">' +
              '<span class="efc-raijin-bell__km">km ' +
              a.km +
              "</span>" +
              '<span class="efc-raijin-bell__meta">' +
              fmtDay(a.day) +
              " · " +
              mm +
              extra +
              "</span>" +
              outcomeBadge(a.outcome, a.kind) +
              "</div>"
            );
          })
          .join("");
    }

    function fetchAlerts(thenOpen) {
      if (!url || loading) return;
      loading = true;
      body.innerHTML =
        '<p class="efc-muted" style="font-size:0.75rem;margin:0.65rem 0.9rem">' +
        (langPt ? "Carregando…" : "Loading…") +
        "</p>";
      fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          if (!r.ok) throw new Error("http " + r.status);
          return r.json();
        })
        .then(function (data) {
          loaded = true;
          render(data || {});
          if (thenOpen) setOpen(true);
        })
        .catch(function () {
          body.innerHTML =
            '<p class="efc-muted" style="font-size:0.75rem;margin:0.65rem 0.9rem">' +
            (langPt ? "Não foi possível carregar os alertas Raijin." : "Could not load Raijin alerts.") +
            "</p>";
          if (thenOpen) setOpen(true);
        })
        .finally(function () {
          loading = false;
        });
    }

    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var open = btn.getAttribute("aria-expanded") === "true";
      if (open) {
        setOpen(false);
        return;
      }
      if (!loaded) fetchAlerts(true);
      else setOpen(true);
    });

    document.addEventListener("click", function (ev) {
      if (!wrap.contains(ev.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") setOpen(false);
    });

    // Prefetch leve para o ponto vermelho (sem abrir o painel).
    window.setTimeout(function () {
      if (!loaded) fetchAlerts(false);
    }, 1200);
  }

  function initHomeToolbarForm() {
    var form = document.getElementById("efc-home-toolbar-form");
    if (!form || form.dataset.bound === "1") return;
    form.dataset.bound = "1";
    syncToolbarDateDisplay(form);
    var dateIn = form.querySelector('input[name="date"]');
    if (dateIn) {
      dateIn.addEventListener("change", function () {
        syncToolbarDateDisplay(form);
        updateMapObservationCaption();
        refreshHomeAccumViews(form);
      });
    }
    // Clique no rótulo (input display:none) + change no radio.
    form.querySelectorAll(".dj-segmented__item").forEach(function (item) {
      item.addEventListener("click", function () {
        var radio = item.querySelector('input[type="radio"]');
        if (!radio || radio.disabled) return;
        window.setTimeout(function () {
          form.querySelectorAll(".dj-segmented__item").forEach(function (el) {
            el.classList.toggle("is-selected", !!el.querySelector("input:checked"));
          });
          if (radio.name === "accum" && radio.checked) {
            updateMapObservationCaption();
            refreshHomeAccumViews(form);
          }
        }, 0);
      });
    });
    form.querySelectorAll('.dj-segmented input[type="radio"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        form.querySelectorAll(".dj-segmented__item").forEach(function (el) {
          el.classList.toggle("is-selected", !!el.querySelector("input:checked"));
        });
        if (radio.name === "accum") {
          updateMapObservationCaption();
          refreshHomeAccumViews(form);
        }
      });
    });
    if (document.body.classList.contains("efc-ui-graphite")) {
      form.addEventListener("submit", function (ev) {
        var boot = document.getElementById("portal-home-bootstrap");
        if (!boot || typeof htmx === "undefined") return;
        ev.preventDefault();
        var langPt = mapLangPt();
        var accumVal = String(readToolbarAccumHours(form));
        var dateVal = readToolbarDateIso(form);
        var bootUrl = boot.getAttribute("hx-get") || "";
        var bootBase = bootUrl.split("?")[0];
        var fullUrl =
          bootBase +
          "?date=" +
          encodeURIComponent(dateVal) +
          "&accum=" +
          encodeURIComponent(accumVal) +
          "&force=1";
        var indUrl = applyToolbarPeriodToIndicatorsUrl(
          boot.getAttribute("data-indicators-url") || "",
          form,
          document.getElementById("efc-acc-alerts-controls")
        );
        if (indUrl.indexOf("force=") < 0) {
          indUrl += (indUrl.indexOf("?") >= 0 ? "&" : "?") + "force=1";
        }
        boot.setAttribute("data-indicators-url", indUrl);
        var mapTpl = boot.getAttribute("data-map-url") || "";
        var mapBase = mapTpl.split("?")[0] || "";
        if (mapBase) {
          boot.setAttribute(
            "data-map-url",
            mapBase +
              "?date=" +
              encodeURIComponent(dateVal) +
              "&accum=" +
              encodeURIComponent(accumVal)
          );
        }
        boot.setAttribute("data-force-refresh", "1");
        boot.removeAttribute("data-parallel-started");
        boot.removeAttribute("data-indicators-loading");
        boot.removeAttribute("data-map-loading");
        var hero = document.getElementById("home-day-strip");
        if (hero) {
          hero.innerHTML =
            '<div class="dj-hero dj-hero--ok dj-hero--loading" aria-live="polite"><div class="dj-hero__inner"><span class="dj-hero__status-icon" aria-hidden="true">◐</span><div class="dj-hero__copy"><strong class="dj-hero__title">' +
            (langPt ? "Atualizando dados…" : "Updating data…") +
            '</strong><p class="dj-hero__meta">' +
            (langPt ? "BigQuery — pode levar até 1 minuto" : "BigQuery — may take up to 1 minute") +
            "</p></div></div></div>";
        }
        try {
          var u = new URL(window.location.href);
          u.searchParams.set("date", dateVal);
          u.searchParams.set("accum", accumVal);
          window.history.replaceState({}, "", u.pathname + u.search + u.hash);
        } catch (e) { /* ignore */ }
        startPortalHomeParallelLoads();
        htmx.ajax("GET", fullUrl, { swap: "none", source: boot });
      });
    }
  }

  function upsertQueryParam(url, key, value) {
    if (!url) return url;
    var parts = url.split("?");
    var base = parts[0];
    var qs = parts[1] || "";
    var params = {};
    qs.split("&").forEach(function (pair) {
      if (!pair) return;
      var kv = pair.split("=");
      var k = decodeURIComponent((kv[0] || "").replace(/\+/g, " "));
      if (!k) return;
      params[k] = kv.length > 1 ? kv.slice(1).join("=") : "";
    });
    params[key] = encodeURIComponent(value);
    var out = Object.keys(params)
      .map(function (k) {
        return k + "=" + params[k];
      })
      .join("&");
    return base + (out ? "?" + out : "");
  }

  function deleteQueryParams(url, keys) {
    if (!url || !keys || !keys.length) return url;
    var parts = url.split("?");
    var base = parts[0];
    var qs = parts[1] || "";
    if (!qs) return base;
    var drop = {};
    keys.forEach(function (k) {
      drop[String(k)] = 1;
    });
    var kept = [];
    qs.split("&").forEach(function (pair) {
      if (!pair) return;
      var kv = pair.split("=");
      var k = decodeURIComponent((kv[0] || "").replace(/\+/g, " "));
      if (!k || drop[k]) return;
      kept.push(pair);
    });
    return base + (kept.length ? "?" + kept.join("&") : "");
  }

  function readToolbarKm(form) {
    var root = form || document;
    var kmIn =
      root.querySelector("#home-km") ||
      root.querySelector('input[name="km"]') ||
      document.getElementById("home-km");
    if (kmIn && kmIn.value != null && String(kmIn.value).trim() !== "") {
      return String(kmIn.value).trim();
    }
    var boot =
      document.getElementById("efc-stitch-home-boot") ||
      document.getElementById("portal-home-bootstrap");
    if (boot) {
      var dk = boot.getAttribute("data-fc-km");
      if (dk != null && String(dk).trim() !== "") return String(dk).trim();
    }
    return "";
  }

  /** Alinha ?date=&accum=&days= (ou start/end custom) na URL dos indicadores. */
  function applyToolbarPeriodToIndicatorsUrl(indUrl, form, controls) {
    var dateVal = readToolbarDateIso(form);
    var accumVal = String(readToolbarAccumHours(form));
    var kmVal = readToolbarKm(form);
    indUrl = (indUrl || "/fragments/home/indicators/").replace(/&amp;/g, "&");
    indUrl = deleteQueryParams(indUrl, [
      "date",
      "accum",
      "days",
      "start",
      "end",
      "date_start",
      "date_end",
      "period",
      "period_mode",
      "year",
      "km",
    ]);
    indUrl = upsertQueryParam(upsertQueryParam(indUrl, "date", dateVal), "accum", accumVal);
    if (kmVal) {
      indUrl = upsertQueryParam(indUrl, "km", kmVal);
    }
    var st = readPeriodState(controls);
    if (st.mode === "custom" && st.start && st.end) {
      indUrl = upsertQueryParam(indUrl, "period_mode", "custom");
      indUrl = upsertQueryParam(indUrl, "start", st.start);
      indUrl = upsertQueryParam(indUrl, "end", st.end);
      indUrl = upsertQueryParam(indUrl, "period", "range_" + st.start + "_" + st.end);
    } else {
      var days = String(accumHoursToDays(accumVal));
      indUrl = upsertQueryParam(indUrl, "days", days);
      indUrl = upsertQueryParam(indUrl, "period", "days_" + days);
    }
    return indUrl.replace(/&amp;/g, "&");
  }

  var _accumRefreshTimer = null;
  function refreshHomeAccumViews(form) {
    if (!form) return;
    if (_accumRefreshTimer) window.clearTimeout(_accumRefreshTimer);
    _accumRefreshTimer = window.setTimeout(function () {
      _accumRefreshTimer = null;
      refreshHomeAccumViewsNow(form);
    }, 30);
  }

  function readToolbarDateIso(form) {
    var root = form || document;
    var dateIn = root.querySelector('input[name="date"]');
    var raw = dateIn && dateIn.value ? String(dateIn.value).trim() : "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
    // Alguns browsers rejeitam value BR em type=date → .value fica vazio.
    var m = /^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})$/.exec(raw);
    if (m) {
      var dd = ("0" + m[1]).slice(-2);
      var mm = ("0" + m[2]).slice(-2);
      return m[3] + "-" + mm + "-" + dd;
    }
    var boot =
      document.getElementById("efc-stitch-home-boot") ||
      document.getElementById("portal-home-bootstrap");
    var fromBoot = "";
    if (boot) {
      var ind = boot.getAttribute("data-indicators-url") || "";
      var map = boot.getAttribute("data-map-url") || "";
      var hit = /[?&]date=(\d{4}-\d{2}-\d{2})/.exec(ind) || /[?&]date=(\d{4}-\d{2}-\d{2})/.exec(map);
      if (hit) fromBoot = hit[1];
    }
    if (fromBoot) return fromBoot;
    try {
      var u = new URL(window.location.href);
      var q = u.searchParams.get("date") || "";
      if (/^\d{4}-\d{2}-\d{2}$/.test(q)) return q;
    } catch (e) { /* ignore */ }
    return "";
  }

  var _mapRefreshGen = 0;
  function refreshHomeAccumViewsNow(form) {
    if (!form) return;
    syncToolbarDateDisplay(form);
    var dateVal = readToolbarDateIso(form);
    var accumVal = String(readToolbarAccumHours(form));
    if (!dateVal) {
      console.warn("[efc] janela refresh aborted: missing date");
      return;
    }

    var stitchBoot = document.getElementById("efc-stitch-home-boot");
    var portalBoot = document.getElementById("portal-home-bootstrap");
    var boot = stitchBoot || portalBoot;
    if (!boot) return;

    var langPt = mapLangPt();
    var accControls = document.getElementById("efc-acc-alerts-controls");
    if (accControls) {
      syncPeriodDaysHidden(accControls);
    }

    // Portal: reusa o fluxo Apply (bootstrap completo).
    if (portalBoot && document.body.classList.contains("efc-ui-graphite") && !stitchBoot) {
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
      return;
    }

    if (typeof htmx === "undefined" || !htmx.ajax) {
      try {
        var jump = new URL(window.location.href);
        jump.searchParams.set("date", dateVal);
        jump.searchParams.set("accum", accumVal);
        window.location.assign(jump.pathname + jump.search + jump.hash);
      } catch (e) {
        window.location.reload();
      }
      return;
    }

    var refreshGen = ++_mapRefreshGen;
    boot.setAttribute("data-refresh-gen", String(refreshGen));

    var     indUrl = applyToolbarPeriodToIndicatorsUrl(
      boot.getAttribute("data-indicators-url") || "/fragments/home/indicators/",
      form,
      accControls
    );
    indUrl = upsertQueryParam(indUrl, "force", "1");
    var mapUrlStored = upsertQueryParam(
      upsertQueryParam(
        (boot.getAttribute("data-map-url") || "/fragments/home/map/").replace(/[?&]_=[^&]*/g, "").replace(/\?$/, ""),
        "date",
        dateVal
      ),
      "accum",
      accumVal
    ).replace(/&amp;/g, "&");
    mapUrlStored = upsertQueryParam(mapUrlStored, "force", "1");
    mapUrlStored = appendSuscPrefToUrl(mapUrlStored);
    var mapUrl = upsertQueryParam(mapUrlStored, "_", String(Date.now()) + "-" + refreshGen);
    boot.setAttribute("data-indicators-url", indUrl);
    boot.setAttribute("data-map-url", mapUrlStored);

    var mapCaption = document.getElementById("efc-map-observation-label");
    if (mapCaption) {
      mapCaption.setAttribute("data-date-display", formatDateBr(dateVal));
    }

    var mapPanel = document.getElementById("home-map-panel");
    if (mapPanel) {
      mapPanel.setAttribute("hx-get", mapUrlStored);
      mapPanel.setAttribute("data-refresh-gen", String(refreshGen));
      try {
        if (typeof htmx.trigger === "function") htmx.trigger(mapPanel, "htmx:abort");
      } catch (eAbort) { /* ignore */ }
      mapPanel.innerHTML =
        '<div class="flex items-center justify-center h-full text-on-surface-variant text-body-md" data-map-pending="' +
        refreshGen +
        '">' +
        (langPt ? "Atualizando mapa…" : "Updating map…") +
        "</div>";
    }
    var indSlot = document.getElementById("home-indicators");
    if (indSlot) {
      indSlot.setAttribute("hx-get", indUrl);
      try {
        if (typeof htmx.trigger === "function") htmx.trigger(indSlot, "htmx:abort");
      } catch (eIndAbort) { /* ignore */ }
      indSlot.innerHTML =
        '<div class="p-4"><p class="efc-muted" style="font-size:0.72rem;margin:0">' +
        (langPt ? "Atualizando indicadores…" : "Updating indicators…") +
        "</p></div>";
    }
    var statusEl = document.getElementById("home-day-strip");
    var statusUrl = "";
    if (statusEl) {
      statusUrl = statusEl.getAttribute("hx-get") || "/fragments/home/status/";
      statusUrl = upsertQueryParam(upsertQueryParam(statusUrl, "date", dateVal), "accum", accumVal);
      statusUrl = upsertQueryParam(statusUrl, "force", "1");
      statusUrl = statusUrl.replace(/&amp;/g, "&");
      statusEl.setAttribute("hx-get", statusUrl);
      try {
        if (typeof htmx.trigger === "function") htmx.trigger(statusEl, "htmx:abort");
      } catch (eStAbort) { /* ignore */ }
    }

    // Bolhas Raijin / URL: janela → days, com fim = data da toolbar.
    if (accControls) {
      syncHomePeriodToUrl(accControls);
    }
    try {
      var u = new URL(window.location.href);
      u.searchParams.set("date", dateVal);
      u.searchParams.set("accum", accumVal);
      window.history.replaceState({}, "", u.pathname + u.search + u.hash);
    } catch (e) { /* ignore */ }

    if (mapPanel && mapUrl) {
      boot.removeAttribute("data-map-loading");
      htmx.ajax("GET", mapUrl, {
        target: "#home-map-panel",
        swap: "innerHTML",
        source: mapPanel,
        timeout: 180000,
      });
    }
    if (indSlot && indUrl) {
      htmx.ajax("GET", indUrl, {
        target: "#home-indicators",
        swap: "innerHTML",
        timeout: 90000,
      });
    }
    if (statusEl && statusUrl) {
      htmx.ajax("GET", statusUrl, {
        target: "#home-day-strip",
        swap: "innerHTML",
        source: statusEl,
        timeout: 90000,
      });
    }
    if (accAlertsMapApi && accAlertsMapApi.invalidateCache) {
      accAlertsMapApi.invalidateCache();
    }
    updateMapObservationCaption();
  }

  function scrollConsultarChatToEnd(smooth) {
    var chat = document.getElementById("efc-consultar-chat");
    if (!chat || !chat.querySelector(".efc-chat__msg")) return;
    var end = document.getElementById("efc-consultar-chat-end");
    var behavior = smooth ? "smooth" : "auto";
    if (end && typeof end.scrollIntoView === "function") {
      end.scrollIntoView({ block: "end", behavior: behavior });
    } else {
      chat.scrollTo({ top: chat.scrollHeight, behavior: behavior });
    }
  }

  function scheduleConsultarChatScroll() {
    scrollConsultarChatToEnd(false);
    window.setTimeout(function () {
      scrollConsultarChatToEnd(false);
    }, 100);
    window.setTimeout(function () {
      scrollConsultarChatToEnd(false);
    }, 400);
    window.setTimeout(function () {
      scrollConsultarChatToEnd(false);
    }, 1000);
  }

  function initConsultarPage() {
    if (document.body.dataset.consultarInit === "1") return;
    document.body.dataset.consultarInit = "1";

    var mainForm = document.getElementById("efc-consultar-form");
    if (!mainForm) return;

    var submitBtn = document.getElementById("efc-consultar-submit");
    var statusEl = document.getElementById("efc-consultar-status");
    var progressEl = document.getElementById("efc-consultar-progress");
    var tickTimer = null;

    function setLoading(on) {
      mainForm.classList.toggle("is-loading", !!on);
      if (submitBtn) {
        submitBtn.textContent = on ? "Consultando…" : "Enviar";
        submitBtn.setAttribute("aria-busy", on ? "true" : "false");
      }
      mainForm.querySelectorAll(".efc-example-chip").forEach(function (btn) {
        btn.disabled = !!on;
      });
      if (statusEl) statusEl.hidden = !on;
      if (progressEl) {
        progressEl.hidden = !on;
        if (on) {
          progressEl.removeAttribute("value");
        } else {
          progressEl.value = 0;
        }
      }
      if (on) {
        var pct = 12;
        if (progressEl) progressEl.value = pct;
        tickTimer = window.setInterval(function () {
          pct = Math.min(92, pct + 4 + Math.random() * 6);
          if (progressEl) progressEl.value = pct;
        }, 280);
      } else if (tickTimer) {
        window.clearInterval(tickTimer);
        tickTimer = null;
      }
    }

    function bindForm(form) {
      if (form.dataset.consultarBound === "1") return;
      form.dataset.consultarBound = "1";
      form.addEventListener("submit", function () {
        try {
          sessionStorage.setItem("efcConsultarScrollEnd", "1");
        } catch (e) { /* ignore */ }
        window.setTimeout(function () {
          setLoading(true);
          scrollConsultarChatToEnd(true);
        }, 0);
      });
    }

    setLoading(false);
    bindForm(mainForm);
    mainForm.querySelectorAll(".efc-example-chip").forEach(function (btn) {
      btn.addEventListener("click", function () {
        try {
          sessionStorage.setItem("efcConsultarScrollEnd", "1");
        } catch (e) { /* ignore */ }
      });
    });

    var shouldScroll = false;
    try {
      shouldScroll = sessionStorage.getItem("efcConsultarScrollEnd") === "1";
      if (shouldScroll) sessionStorage.removeItem("efcConsultarScrollEnd");
    } catch (e) { /* ignore */ }
    if (shouldScroll || document.querySelector("#efc-consultar-chat .efc-chat__msg")) {
      scheduleConsultarChatScroll();
    }

    window.addEventListener("pageshow", function (ev) {
      if (ev.persisted) {
        setLoading(false);
        scheduleConsultarChatScroll();
      }
    });
  }

  var IA_CHAT_LS_KEY = "efcRaijinChatHistory_v1";
  var IA_CHAT_MIN_KEY = "efcRaijinChatMinimized_v1";

  function initRaijinChatDrawer() {
    var wrap = document.getElementById("efc-stitch-fab");
    var drawer = document.getElementById("efc-ia-chat-drawer");
    var openBtn = document.getElementById("efc-ia-fab-open");
    var restoreBtn = document.getElementById("efc-ia-fab-restore");
    if (!wrap || !drawer || !openBtn) return;
    if (wrap.dataset.iaChatInit === "1") return;
    wrap.dataset.iaChatInit = "1";

    var messagesEl = document.getElementById("efc-ia-chat-messages");
    var emptyEl = document.getElementById("efc-ia-chat-empty");
    var endEl = document.getElementById("efc-ia-chat-end");
    var form = document.getElementById("efc-ia-chat-form");
    var input = document.getElementById("efc-ia-chat-input");
    var submitBtn = document.getElementById("efc-ia-chat-submit");
    var statusEl = document.getElementById("efc-ia-chat-status");
    var newBtn = document.getElementById("efc-ia-chat-new");
    var minBtn = document.getElementById("efc-ia-chat-minimize");
    var closeBtn = document.getElementById("efc-ia-chat-close");
    var langPt = (drawer.getAttribute("data-lang") || "pt") !== "en";
    var askUrl = drawer.getAttribute("data-ask-url") || "";
    var historyUrl = drawer.getAttribute("data-history-url") || "";
    var clearUrl = drawer.getAttribute("data-clear-url") || "";
    var csrfInput = form ? form.querySelector('input[name="csrfmiddlewaretoken"]') : null;
    var loading = false;
    var history = [];
    var lastFocus = null;
    var HISTORY_CAP = 60;

    function csrfToken() {
      return csrfInput ? csrfInput.value : "";
    }

    function saveLocal(hist) {
      try {
        var slim = (hist || []).slice(-HISTORY_CAP);
        localStorage.setItem(IA_CHAT_LS_KEY, JSON.stringify(slim));
      } catch (e) { /* ignore */ }
    }

    function loadLocal() {
      try {
        var raw = localStorage.getItem(IA_CHAT_LS_KEY);
        if (!raw) return [];
        var parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
      } catch (e) {
        return [];
      }
    }

    function setMinimized(on) {
      wrap.classList.toggle("is-minimized", !!on);
      if (restoreBtn) restoreBtn.hidden = !on;
      try {
        localStorage.setItem(IA_CHAT_MIN_KEY, on ? "1" : "0");
      } catch (e) { /* ignore */ }
    }

    function trapFocus(ev) {
      if (!drawer || drawer.hasAttribute("hidden")) return;
      if (ev.key !== "Tab") return;
      var focusables = drawer.querySelectorAll(
        'button:not([disabled]), [href], textarea, input, select, [tabindex]:not([tabindex="-1"])'
      );
      if (!focusables.length) return;
      var first = focusables[0];
      var last = focusables[focusables.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    }

    function setOpen(open) {
      openBtn.setAttribute("aria-expanded", open ? "true" : "false");
      drawer.setAttribute("aria-modal", open ? "true" : "false");
      if (open) {
        lastFocus = document.activeElement;
        drawer.removeAttribute("hidden");
        setMinimized(false);
        document.addEventListener("keydown", trapFocus, true);
        if (input) window.setTimeout(function () { input.focus(); }, 40);
        // Histórico no servidor só ao abrir — evita reescrever cookie na carga da home (corrida HTMX).
        if (drawer.getAttribute("data-history-loaded") !== "1") {
          drawer.setAttribute("data-history-loaded", "1");
          loadHistory();
        }
      } else {
        drawer.setAttribute("hidden", "");
        document.removeEventListener("keydown", trapFocus, true);
        if (lastFocus && typeof lastFocus.focus === "function") {
          try { lastFocus.focus(); } catch (e) { /* ignore */ }
        }
      }
    }

    function scrollEnd() {
      if (endEl && typeof endEl.scrollIntoView === "function") {
        endEl.scrollIntoView({ block: "end", behavior: "smooth" });
      } else if (messagesEl) {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    }

    function escapeHtml(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function welcomeHtml() {
      return (
        '<div class="efc-ia-welcome" id="efc-ia-chat-empty">' +
        '<span class="efc-ia-welcome__icon" aria-hidden="true">✦</span>' +
        "<strong>Raijin</strong>" +
        "<p>" +
        (langPt
          ? "Ajudo com chuva, limiares e modelos no corredor EFC."
          : "I help with rainfall, thresholds and models on the EFC corridor.") +
        "</p></div>"
      );
    }

    function chipsHtml(chips) {
      if (!chips || !chips.length) return "";
      var html = '<div class="efc-chat__chips">';
      chips.forEach(function (chip) {
        if (!chip) return;
        if (chip.href) {
          html +=
            '<a class="efc-example-chip" href="' +
            escapeHtml(chip.href) +
            '">' +
            escapeHtml(chip.label || "") +
            "</a>";
        } else if (chip.q) {
          html +=
            '<button type="button" class="efc-example-chip efc-ia-follow-chip" data-q="' +
            escapeHtml(chip.q) +
            '">' +
            escapeHtml(chip.label || chip.q) +
            "</button>";
        }
      });
      html += "</div>";
      return html;
    }

    function msgHtml(msg) {
      var role = msg.role === "assistant" ? "assistant" : "user";
      var html =
        '<article class="efc-chat__msg efc-chat__msg--' +
        role +
        (msg.welcome ? " efc-chat__msg--welcome" : "") +
        '">';
      if (role === "assistant" && msg.sql_origin_label) {
        html +=
          '<span class="efc-chat__origin">' +
          escapeHtml(msg.sql_origin_label) +
          "</span>";
      }
      if (role === "user") {
        html +=
          '<p class="efc-chat__bubble">' +
          escapeHtml(msg.text).replace(/\n/g, "<br>") +
          "</p>";
      } else if (msg.text_html) {
        html += '<div class="efc-chat__interpret">' + msg.text_html + "</div>";
      } else if (msg.text) {
        html +=
          '<div class="efc-chat__interpret"><p>' +
          escapeHtml(msg.text).replace(/\n/g, "<br>") +
          "</p></div>";
      }
      if (role === "assistant" && msg.table_html) {
        html += '<div class="efc-consultar-table">' + msg.table_html + "</div>";
      }
      if (role === "assistant" && msg.chart_json) {
        var plotPayload =
          typeof msg.chart_json === "string"
            ? msg.chart_json
            : JSON.stringify(msg.chart_json);
        html +=
          '<div class="efc-plot efc-plot--chat" data-plot="' +
          escapeHtml(plotPayload) +
          '"></div>';
      }
      if (role === "assistant" && msg.sql) {
        html +=
          '<details class="efc-chat__sql"><summary>' +
          (langPt ? "Detalhe técnico" : "Technical detail") +
          '</summary><pre class="efc-chat__sql-code">' +
          escapeHtml(msg.sql) +
          "</pre></details>";
      }
      if (role === "assistant" && msg.previsao_url) {
        html +=
          '<p class="efc-consultar-actions"><a class="efc-stitch-btn efc-stitch-btn--ghost efc-stitch-btn--sm" href="' +
          escapeHtml(msg.previsao_url) +
          '">' +
          (langPt ? "Previsão deste km" : "Forecast for this km") +
          "</a></p>";
      }
      if (role === "assistant") {
        html += chipsHtml(msg.chips);
      }
      html += "</article>";
      return html;
    }

    function bindFollowChips(root) {
      (root || messagesEl).querySelectorAll(".efc-ia-follow-chip").forEach(function (btn) {
        if (btn.dataset.bound === "1") return;
        btn.dataset.bound = "1";
        btn.addEventListener("click", function () {
          var q = btn.getAttribute("data-q") || "";
          if (input) input.value = q;
          ask(q);
        });
      });
    }

    function renderHistory(hist) {
      history = Array.isArray(hist) ? hist.slice(-HISTORY_CAP) : [];
      if (!messagesEl) return;
      var html = "";
      if (!history.length) {
        html = welcomeHtml();
      } else {
        history.forEach(function (msg) {
          html += msgHtml(msg);
        });
      }
      html += '<div id="efc-ia-chat-end" aria-hidden="true"></div>';
      messagesEl.innerHTML = html;
      emptyEl = document.getElementById("efc-ia-chat-empty");
      endEl = document.getElementById("efc-ia-chat-end");
      bindFollowChips(messagesEl);
      if (typeof renderPlotsWithRetry === "function") {
        renderPlotsWithRetry(messagesEl, 0);
      } else if (typeof renderPlots === "function") {
        renderPlots(messagesEl);
      }
      saveLocal(history);
      scrollEnd();
    }

    function setLoading(on, label) {
      loading = !!on;
      if (submitBtn) {
        submitBtn.disabled = !!on;
        submitBtn.textContent = on
          ? langPt
            ? "Consultando…"
            : "Querying…"
          : langPt
            ? "Enviar"
            : "Send";
      }
      if (statusEl) {
        statusEl.hidden = !on;
        if (on && label) statusEl.textContent = label;
        else if (on) {
          statusEl.textContent = langPt
            ? "Consultando dados do corredor…"
            : "Querying corridor data…";
        }
      }
      if (form) form.classList.toggle("is-loading", !!on);
    }

    function ensureTypingEl() {
      var el = document.getElementById("efc-ia-typing");
      if (el) return el;
      el = document.createElement("div");
      el.id = "efc-ia-typing";
      el.className = "efc-ia-typing";
      el.innerHTML =
        '<span class="efc-ia-typing__step"></span><div class="efc-ia-typing__stream"></div>';
      if (endEl && endEl.parentNode) {
        endEl.parentNode.insertBefore(el, endEl);
      } else if (messagesEl) {
        messagesEl.appendChild(el);
      }
      return el;
    }

    function parseSseChunk(buffer, onEvent) {
      var parts = buffer.split("\n\n");
      var rest = parts.pop() || "";
      parts.forEach(function (block) {
        var lines = block.split("\n");
        var event = "message";
        var dataLines = [];
        lines.forEach(function (line) {
          if (line.indexOf("event:") === 0) event = line.slice(6).trim();
          else if (line.indexOf("data:") === 0) dataLines.push(line.slice(5).trim());
        });
        if (!dataLines.length) return;
        var raw = dataLines.join("\n");
        var data = raw;
        try {
          data = JSON.parse(raw);
        } catch (e) { /* keep string */ }
        onEvent(event, data);
      });
      return rest;
    }

    function askJsonFallback(q) {
      var body = new URLSearchParams();
      body.set("pergunta", q);
      body.set("format", "json");
      body.set("csrfmiddlewaretoken", csrfToken());
      return fetch(askUrl, {
        method: "POST",
        credentials: "same-origin",
        redirect: "manual",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: body.toString(),
      }).then(function (r) {
        if (r.type === "opaqueredirect" || r.status === 0) {
          redirectToMonitorLoginOnce();
          throw new Error("HTTP 401");
        }
        if (r.status === 401) {
          return r.json().catch(function () { return null; }).then(function (body) {
            var loginUrl = (body && body.login_url) || monitorLoginRedirectUrl();
            if (!monitorAuthRedirecting) {
              monitorAuthRedirecting = true;
              window.location.replace(loginUrl);
            }
            throw new Error("HTTP 401");
          });
        }
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      });
    }

    function ask(pergunta) {
      var q = String(pergunta || "").trim();
      if (!q || !askUrl || loading) return;
      setLoading(true, langPt ? "Preparando consulta…" : "Preparing query…");
      var typing = ensureTypingEl();
      var stepEl = typing.querySelector(".efc-ia-typing__step");
      var streamEl = typing.querySelector(".efc-ia-typing__stream");
      if (stepEl) {
        stepEl.textContent = langPt
          ? "Consultando dados do corredor…"
          : "Querying corridor data…";
      }
      if (streamEl) streamEl.textContent = "";
      scrollEnd();

      var body = new URLSearchParams();
      body.set("pergunta", q);
      body.set("format", "json");
      body.set("stream", "1");
      body.set("csrfmiddlewaretoken", csrfToken());

      fetch(askUrl, {
        method: "POST",
        credentials: "same-origin",
        redirect: "manual",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: body.toString(),
      })
        .then(function (r) {
          if (r.type === "opaqueredirect" || r.status === 0) {
            redirectToMonitorLoginOnce();
            throw new Error("HTTP 401");
          }
          if (r.status === 401) {
            return r.json().catch(function () { return null; }).then(function (body) {
              var loginUrl = (body && body.login_url) || monitorLoginRedirectUrl();
              if (!monitorAuthRedirecting) {
                monitorAuthRedirecting = true;
                window.location.replace(loginUrl);
              }
              throw new Error("HTTP 401");
            });
          }
          var ct = (r.headers.get("content-type") || "").toLowerCase();
          if (!r.ok) throw new Error("http " + r.status);
          if (ct.indexOf("text/event-stream") === -1) {
            return r.json().then(function (data) {
              renderHistory((data && data.history) || []);
            });
          }
          if (!r.body || !r.body.getReader) {
            return askJsonFallback(q).then(function (data) {
              renderHistory((data && data.history) || []);
            });
          }
          var reader = r.body.getReader();
          var decoder = new TextDecoder();
          var buf = "";
          var doneHistory = null;

          function pump() {
            return reader.read().then(function (res) {
              if (res.done) {
                if (doneHistory) renderHistory(doneHistory);
                return;
              }
              buf += decoder.decode(res.value, { stream: true });
              buf = parseSseChunk(buf, function (event, data) {
                if (event === "step" && stepEl) {
                  stepEl.textContent =
                    (data && data.label) ||
                    (langPt ? "Consultando…" : "Querying…");
                  setLoading(true, stepEl.textContent);
                } else if (event === "token" && streamEl) {
                  streamEl.textContent += (data && data.text) || "";
                  scrollEnd();
                } else if (event === "done") {
                  doneHistory = (data && data.history) || [];
                } else if (event === "error") {
                  throw new Error((data && data.error) || "stream error");
                }
              });
              return pump();
            });
          }
          return pump();
        })
        .catch(function () {
          return askJsonFallback(q)
            .then(function (data) {
              renderHistory((data && data.history) || []);
            })
            .catch(function () {
              var fallback = history.slice();
              fallback = fallback.filter(function (m) {
                return !m.welcome;
              });
              fallback.push({ role: "user", text: q });
              fallback.push({
                role: "assistant",
                text: langPt
                  ? "Não foi possível obter resposta agora. Tente de novo."
                  : "Could not get a reply right now. Please try again.",
                chips: [
                  {
                    label: langPt ? "Top 20 km 30d" : "Top 20 km 30d",
                    q: "Top 20 km com maior pico de chuva 24 h nos últimos 30 dias",
                  },
                ],
              });
              renderHistory(fallback);
            });
        })
        .finally(function () {
          var typingGone = document.getElementById("efc-ia-typing");
          if (typingGone && typingGone.parentNode) typingGone.parentNode.removeChild(typingGone);
          setLoading(false);
          if (input) {
            input.value = "";
            input.focus();
          }
        });
    }

    function loadHistory() {
      var local = loadLocal();
      if (local.length) renderHistory(local);
      if (!historyUrl) {
        if (!local.length) renderHistory([]);
        return;
      }
      fetch(historyUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (r) {
          if (!r.ok) throw new Error("http " + r.status);
          return r.json();
        })
        .then(function (data) {
          var serverHist = (data && data.history) || [];
          if (serverHist.length) renderHistory(serverHist);
          else if (!local.length) renderHistory([]);
        })
        .catch(function () {
          if (!local.length) renderHistory([]);
        });
    }

    openBtn.addEventListener("click", function (ev) {
      ev.preventDefault();
      var open = openBtn.getAttribute("aria-expanded") === "true";
      if (open) setOpen(false);
      else setOpen(true);
    });

    if (restoreBtn) {
      restoreBtn.addEventListener("click", function (ev) {
        ev.preventDefault();
        setOpen(true);
      });
    }

    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        setOpen(false);
        setMinimized(false);
      });
    }

    if (minBtn) {
      minBtn.addEventListener("click", function () {
        setOpen(false);
        setMinimized(true);
      });
    }

    if (newBtn) {
      newBtn.addEventListener("click", function () {
        function wipe() {
          renderHistory([]);
          if (input) input.focus();
        }
        if (!clearUrl) {
          wipe();
          return;
        }
        var body = new URLSearchParams();
        body.set("format", "json");
        body.set("csrfmiddlewaretoken", csrfToken());
        fetch(clearUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
          },
          body: body.toString(),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            renderHistory((data && data.history) || []);
          })
          .catch(function () {
            wipe();
          });
      });
    }

    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        ask(input ? input.value : "");
      });
    }

    if (input) {
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
          ev.preventDefault();
          ask(input.value);
        }
      });
    }

    drawer.querySelectorAll("#efc-ia-chat-examples .efc-example-chip").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var q = btn.getAttribute("data-q") || btn.textContent || "";
        if (input) input.value = q;
        ask(q);
      });
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && openBtn.getAttribute("aria-expanded") === "true") {
        setOpen(false);
      }
    });

    try {
      if (localStorage.getItem(IA_CHAT_MIN_KEY) === "1") setMinimized(true);
    } catch (e) { /* ignore */ }

    // Só localStorage no boot; fetch do servidor ao abrir o drawer.
    var bootLocal = loadLocal();
    if (bootLocal.length) renderHistory(bootLocal);
  }

  function initBoletimPdfExport() {
    var btn = document.getElementById("efc-boletim-export-pdf");
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", function () {
      window.print();
    });
  }

  function mapIframeWindow() {
    var iframe = document.getElementById("efc-map-iframe");
    return iframe && iframe.contentWindow ? iframe.contentWindow : null;
  }

  function postToMap(payload) {
    var win = mapIframeWindow();
    if (!win) return;
    try {
      win.postMessage(payload, "*");
    } catch (e) {}
  }

  function apiUrl(name, qs) {
    var root = document.body ? document.body.getAttribute("data-monitor-api-root") : null;
    var base = root || "";
    var path = base + name;
    if (qs) path += (path.indexOf("?") >= 0 ? "&" : "?") + qs;
    return path;
  }

  function initMapLayerStrip() {
    var strip = document.getElementById("efc-layer-strip");
    if (!strip || strip.dataset.bound === "1") return;
    strip.dataset.bound = "1";
    var SUSC_KEY = "efc_map_suscetibilidade_on";
    var BASE_KEY = "efc_map_basemap";
    var climaOn = false;
    var climaPoints = null;

    function setBasemap(mode) {
      mode = mode === "sat" ? "sat" : "3d";
      try {
        localStorage.setItem(BASE_KEY, mode);
      } catch (e0) {}
      strip.querySelectorAll("[data-basemap]").forEach(function (btn) {
        btn.classList.toggle("is-active", btn.getAttribute("data-basemap") === mode);
      });
      postToMap({ type: "efc-map-basemap", mode: mode });
    }

    function setSusc(on) {
      try {
        localStorage.setItem(SUSC_KEY, on ? "1" : "0");
      } catch (e1) {}
      var btn = strip.querySelector('[data-overlay="susc"]');
      if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
      postToMap({ type: "efc-map-susc", on: !!on });
      // Recarrega embed com ?susc=1 para trazer raster pesado (mesmo fluxo legado).
      var iframe = document.getElementById("efc-map-iframe");
      if (!iframe) return;
      var src = iframe.getAttribute("src") || iframe.getAttribute("data-embed-src") || "";
      if (!src || src === "about:blank") return;
      try {
        var u = new URL(src, window.location.origin);
        if (on) u.searchParams.set("susc", "1");
        else u.searchParams.delete("susc");
        var next = u.pathname + u.search + u.hash;
        if (iframe.getAttribute("data-embed-src")) {
          iframe.setAttribute("data-embed-src", next);
          if (iframe.getAttribute("src") && iframe.getAttribute("src") !== "about:blank") {
            iframe.setAttribute("src", next);
          }
        } else {
          iframe.setAttribute("src", next);
        }
      } catch (e2) {}
    }

    function climateApiUrl() {
      return strip.getAttribute("data-climate-url") || "/api/home/climate-gfs/";
    }
    function gotoKmApiUrl(km) {
      var base = strip.getAttribute("data-goto-km-url") || "/api/home/goto-km/";
      var join = base.indexOf("?") >= 0 ? "&" : "?";
      return base + join + "km=" + encodeURIComponent(km);
    }

    var climaPayload = null;

    function loadClima(on) {
      var btn = strip.querySelector('[data-overlay="clima"]');
      if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
      climaOn = !!on;
      if (!on) {
        postToMap({ type: "efc-map-clima", on: false, climate: { points: [], grid: [], frames: [] } });
        return;
      }
      // Camada Clima é Leaflet 2D — troca basemap para satélite.
      setBasemap("sat");
      function send(payload) {
        postToMap({
          type: "efc-map-clima",
          on: true,
          climate: payload || {},
          points: (payload && payload.points) || [],
          grid: (payload && payload.grid) || null,
          frames: (payload && payload.frames) || null,
          frame_i: payload && payload.frame_i,
        });
      }
      if (climaPayload) {
        send(climaPayload);
        return;
      }
      fetch(climateApiUrl(), { credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) throw new Error("climate " + r.status);
          return r.json();
        })
        .then(function (data) {
          climaPayload = data || {};
          climaPoints = (data && data.points) || [];
          send(climaPayload);
        })
        .catch(function () {
          send({ points: [], grid: [], frames: [] });
        });
    }

    strip.querySelectorAll("[data-basemap]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setBasemap(btn.getAttribute("data-basemap"));
      });
    });
    var suscBtn = strip.querySelector('[data-overlay="susc"]');
    if (suscBtn) {
      suscBtn.addEventListener("click", function () {
        var on = suscBtn.getAttribute("aria-pressed") !== "true";
        setSusc(on);
      });
    }
    var climaBtn = strip.querySelector('[data-overlay="clima"]');
    if (climaBtn) {
      climaBtn.addEventListener("click", function () {
        loadClima(climaBtn.getAttribute("aria-pressed") !== "true");
      });
    }
    var form = document.getElementById("efc-goto-km-form");
    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var input = document.getElementById("efc-goto-km-input");
        var km = input ? Number(input.value) : NaN;
        if (!isFinite(km)) return;
        fetch(gotoKmApiUrl(km), { credentials: "same-origin" })
          .then(function (r) {
            if (!r.ok) throw new Error("goto " + r.status);
            return r.json();
          })
          .then(function (data) {
            postToMap({
              type: "efc-map-goto-km",
              km: km,
              lat: data && data.lat,
              lon: data && data.lon,
            });
          })
          .catch(function () {
            postToMap({ type: "efc-map-goto-km", km: km });
          });
      });
    }

    var prefBase = "3d";
    try {
      prefBase = localStorage.getItem(BASE_KEY) || "3d";
    } catch (e5) {}
    setBasemap(prefBase);
    var prefSusc = false;
    try {
      prefSusc = localStorage.getItem(SUSC_KEY) === "1";
    } catch (e6) {}
    if (suscBtn) suscBtn.setAttribute("aria-pressed", prefSusc ? "true" : "false");

    window.addEventListener("message", function (ev) {
      if (!ev.data) return;
      if (ev.data.type === "efc-map-ready" || ev.data.type === "efc-map-3d-ready") {
        var mode = prefBase;
        try {
          mode = localStorage.getItem(BASE_KEY) || prefBase;
        } catch (e7) {}
        setBasemap(mode);
        if (climaOn) loadClima(true);
        postToMap({ type: "efc-map-susc", on: prefSusc });
      }
    });
  }

  function boot() {
    function reloadMapOnDateChange() {
      if (accAlertsMapApi && accAlertsMapApi.invalidateCache) {
        accAlertsMapApi.invalidateCache();
        accAlertsMapApi.reattachMap();
        return;
      }
      initAccumulatedAlerts();
    }

    window.efcMonitor = {
      renderPlots: renderPlots,
      initAccumulatedAlerts: initAccumulatedAlerts,
      reloadMapOnDateChange: reloadMapOnDateChange,
      buildPrevisaoDom: buildPrevisaoDom,
      buildPrevisaoHeatmapDom: buildPrevisaoHeatmapDom,
      buildPrevisaoChartsDom: buildPrevisaoChartsDom,
      buildPrevisaoVerificationDom: buildPrevisaoVerificationDom,
      buildPrevisaoKmVerificationDom: buildPrevisaoKmVerificationDom,
    };
    renderPlotsWithRetry(document, 0);
    initIngestionCountdown();
    initBoletinsPage();
    initBoletimPdfExport();
    initMapObservationCaption();
    initMapLegendCollapse();
    initMapLayerStrip();
    initSuscetibilidadeLayerPersist();
    if (!isPortalGraphiteHome()) {
      initAccumulatedAlerts();
    }
    initChartsModal();
    initPrevisaoPage();
    initPrevisaoAtivos();
    initSidebarDrawer();
    initConsultarPage();
    initRaijinChatDrawer();
    initHomeToolbarForm();
    initRaijinBell();
    if (typeof window.efcInitMonitorTour === "function") {
      window.efcInitMonitorTour();
    }
    kickStitchIndicatorsForce();
    setLoadPct(8);
    // Portal Visão Geral: indicadores + mapa junto com bootstrap (sem cascata).
    startPortalHomeParallelLoads();
    initMonitorAuthRedirects();
    document.body.addEventListener("htmx:beforeSwap", function (ev) {
      var xhr = ev.detail && ev.detail.xhr;
      if (!xhr || xhr.status !== 429) return;
      var target = ev.detail.target;
      var key = target && target.id ? target.id : "global";
      var n = parseInt(sessionStorage.getItem("efc429_" + key) || "0", 10);
      if (n < 4) {
        ev.preventDefault();
        sessionStorage.setItem("efc429_" + key, String(n + 1));
        var delay = 2500 * (n + 1);
        var langPt = mapLangPt();
        if (target && target.id === "home-indicators") {
          target.innerHTML =
            '<p class="efc-muted" style="font-size:0.72rem;padding:0.35rem 0">' +
            (langPt ? "Servidor ocupado — tentando de novo…" : "Server busy — retrying…") +
            "</p>";
        }
        window.setTimeout(function () {
          var boot = document.getElementById("portal-home-bootstrap");
          if (target && target.id === "home-indicators" && boot) {
            var indUrl = boot.getAttribute("data-indicators-url");
            if (indUrl && htmx.ajax) {
              htmx.ajax("GET", indUrl, { target: "#home-indicators", swap: "innerHTML" });
            }
          } else if (target && target.id === "home-map-panel" && boot) {
            boot.removeAttribute("data-map-loading");
            loadPortalMapPanel(boot);
          } else {
            window.location.reload();
          }
        }, delay);
        return;
      }
      sessionStorage.removeItem("efc429_" + key);
      ev.preventDefault();
      var langPt = mapLangPt();
      var msg =
        langPt
          ? "Servidor ocupado — aguarde alguns segundos e atualize a página."
          : "Server busy — wait a few seconds and refresh.";
      if (!target || !target.id) return;
      if (target.id === "home-map-panel") {
        target.innerHTML = '<p class="efc-map-unavailable" role="alert">' + msg + "</p>";
      } else if (target.id === "home-alerts-slot") {
        target.innerHTML =
          '<h3 class="dj-alerts__title">' +
          (langPt ? "Trechos em alerta" : "Alert segments") +
          '</h3><div class="dj-alerts__chips"><span class="efc-muted-chip">' +
          msg +
          "</span></div>";
      } else {
        target.innerHTML = '<p class="efc-alert efc-alert--warn" role="alert">' + msg + "</p>";
      }
    });
    document.body.addEventListener("htmx:afterOnLoad", function (ev) {
      var xhr = ev.detail && ev.detail.xhr;
      var url = (xhr && xhr.responseURL) || "";
      if (url.indexOf("fragments/home/bootstrap") < 0) return;
      var boot = document.getElementById("portal-home-bootstrap");
      // Se o load paralelo já disparou, não reinicia indicadores/mapa (evita double-fetch).
      if (boot && boot.getAttribute("data-parallel-started") !== "1") {
        var indUrl = boot.getAttribute("data-indicators-url");
        if (indUrl && typeof htmx !== "undefined" && htmx.ajax) {
          var indSlot = document.getElementById("home-indicators");
          if (indSlot) {
            indSlot.innerHTML =
              '<div class="dj-module dj-module--compact dj-module--metrics dj-module--loading"><p class="efc-muted" style="font-size:0.72rem;padding:0.35rem 0">' +
              (mapLangPt() ? "Carregando indicadores…" : "Loading indicators…") +
              "</p></div>";
          }
          htmx.ajax("GET", indUrl, {
            target: "#home-indicators",
            swap: "innerHTML",
            timeout: 120000,
          });
        }
        loadPortalMapPanel(boot);
      }
      schedulePortalCacheRetry(boot);
      var meta = document.getElementById("portal-bootstrap-meta");
      if (meta && meta.getAttribute("data-cache-ready") === "0" && boot) {
        window.setTimeout(function () {
          var url = boot.getAttribute("hx-get");
          if (url && typeof htmx !== "undefined" && htmx.ajax) {
            htmx.ajax("GET", url, { swap: "none", source: boot });
          }
        }, 45000);
      }
      boot && boot.removeAttribute("data-force-refresh");
      var main = document.getElementById("efc-home-main");
      if (main) main.setAttribute("aria-busy", "false");
      var bar = document.getElementById("efc-load-progress");
      if (bar) bar.classList.add("is-done");
    });
    document.body.addEventListener("htmx:afterSwap", onHtmxAfterSwap);
    document.body.addEventListener("htmx:afterSettle", onHtmxAfterSwap);
    document.body.addEventListener("htmx:timeout", function (ev) {
      var target = ev.detail && ev.detail.target;
      if (!target || !target.id) return;
      var langPt = mapLangPt();
      if (target.id === "home-day-strip") {
        target.innerHTML =
          '<section class="dj-hero dj-hero--watch" aria-live="polite">' +
          '<div class="dj-hero__inner"><span class="dj-hero__status-icon" aria-hidden="true">◐</span>' +
          '<div class="dj-hero__copy"><strong class="dj-hero__title">' +
          (langPt ? "Demorou para carregar" : "Load timed out") +
          "</strong><p class=\"dj-hero__meta\">" +
          (langPt
            ? "BigQuery lento — atualize a página ou troque a data."
            : "BigQuery slow — refresh or pick another date.") +
          "</p></div></div></section>";
        setLoadPct(35);
        return;
      }
      if (target.id === "home-indicators") {
        target.innerHTML =
          '<p class="efc-alert efc-alert--warn" role="alert">' +
          (langPt
            ? "Indicadores demoraram — BigQuery lento. Aguarde ou atualize a página."
            : "Indicators timed out — BigQuery slow. Wait or refresh.") +
          "</p>";
        setLoadPct(60);
        return;
      }
      if (target.id === "home-alerts-slot") {
        target.innerHTML =
          '<h3 class="dj-alerts__title">' +
          (langPt ? "Trechos em alerta" : "Alert segments") +
          '</h3><div class="dj-alerts__chips"><span class="efc-muted-chip">' +
          (langPt ? "Demorou para carregar" : "Load timed out") +
          "</span></div>";
        setLoadPct(45);
        return;
      }
      if (target.id !== "home-map-panel") return;
      target.innerHTML =
        '<p class="efc-map-unavailable" role="alert">' + mapLoadTimeoutMessage(langPt) + "</p>";
      setLoadPct(75);
      var main = document.getElementById("efc-home-main");
      if (main) main.setAttribute("aria-busy", "false");
    });
    document.body.addEventListener("htmx:responseError", function (ev) {
      var target = ev.detail && ev.detail.target;
      if (!target || !target.id) return;
      var langPt = mapLangPt();
      if (target.id === "home-day-strip") {
        target.innerHTML =
          '<section class="dj-hero dj-hero--watch" aria-live="polite">' +
          '<div class="dj-hero__inner"><span class="dj-hero__status-icon" aria-hidden="true">!</span>' +
          '<div class="dj-hero__copy"><strong class="dj-hero__title">' +
          (langPt ? "Erro ao carregar status" : "Status load error") +
          "</strong><p class=\"dj-hero__meta\">" +
          (langPt ? "Atualize a página. Se persistir, verifique credenciais GCP." : "Refresh the page. Check GCP credentials if it persists.") +
          "</p></div></div></section>";
        setLoadPct(35);
        return;
      }
      if (target.id === "home-indicators") {
        target.innerHTML =
          '<p class="efc-alert efc-alert--warn" role="alert">' +
          (langPt ? "Indicadores indisponíveis no momento." : "Indicators unavailable right now.") +
          "</p>";
        setLoadPct(60);
        return;
      }
      if (target.id === "home-alerts-slot") {
        target.innerHTML =
          '<h3 class="dj-alerts__title">' +
          (langPt ? "Trechos em alerta" : "Alert segments") +
          '</h3><div class="dj-alerts__chips"><span class="efc-muted-chip">' +
          (langPt ? "Erro ao carregar" : "Load error") +
          "</span></div>";
        setLoadPct(45);
        return;
      }
      if (target.id !== "home-map-panel") return;
      target.innerHTML =
        '<p class="efc-map-unavailable" role="alert">' +
        (langPt ? "Não foi possível carregar o painel do mapa." : "Could not load the map panel.") +
        "</p>";
      setLoadPct(75);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
