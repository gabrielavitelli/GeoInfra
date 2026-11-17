/**
 * Overlay Clima 2D (Leaflet): nuvem/chuva + partículas de vento + replay GFS.
 * Expõe window.EfcClimaLayer.set(on, payload) / .clear()
 */
(function (global) {
  "use strict";

  var state = {
    map: null,
    on: false,
    grid: [],
    frames: [],
    frameI: 0,
    playing: false,
    timer: null,
    canvas: null,
    pane: null,
    particles: [],
    raf: 0,
    bound: false,
  };

  function findMap() {
    if (!global.L) return null;
    var el = document.querySelector(".folium-map");
    if (el && el.id && global[el.id] && typeof global[el.id].addLayer === "function") {
      return global[el.id];
    }
    for (var k in global) {
      if (k.indexOf("map_") === 0 && global[k] && typeof global[k].addLayer === "function") {
        return global[k];
      }
    }
    return null;
  }

  function ensurePane(map) {
    if (!map.getPane("efcClimaPane")) {
      map.createPane("efcClimaPane");
      map.getPane("efcClimaPane").style.zIndex = 460;
      map.getPane("efcClimaPane").style.pointerEvents = "none";
    }
    return map.getPane("efcClimaPane");
  }

  function ensureCanvas(map) {
    var pane = ensurePane(map);
    if (state.canvas && state.canvas.parentNode === pane) return state.canvas;
    var c = document.createElement("canvas");
    c.className = "efc-clima-canvas";
    c.style.cssText = "position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;";
    pane.appendChild(c);
    state.canvas = c;
    state.pane = pane;
    return c;
  }

  function ensureReplayBar(map) {
    var host = map.getContainer();
    var bar = host.querySelector(".efc-clima-replay");
    if (bar) return bar;
    bar = document.createElement("div");
    bar.className = "efc-clima-replay";
    bar.innerHTML =
      '<button type="button" class="efc-clima-replay__play" aria-label="Play/Pause">▶</button>' +
      '<input type="range" class="efc-clima-replay__scrub" min="0" max="0" value="0" />' +
      '<span class="efc-clima-replay__label">—</span>';
    var style = document.createElement("style");
    style.textContent =
      ".efc-clima-replay{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);z-index:500;" +
      "display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:10px;" +
      "background:rgba(15,23,42,.88);border:1px solid rgba(148,163,184,.4);color:#e2e8f0;" +
      "font:650 11px/1 'IBM Plex Sans',sans-serif;backdrop-filter:blur(8px);box-shadow:0 6px 18px rgba(2,6,23,.35)}" +
      ".efc-clima-replay[hidden]{display:none!important}" +
      ".efc-clima-replay__play{appearance:none;border:0;background:rgba(56,189,248,.2);color:#e0f2fe;" +
      "width:28px;height:28px;border-radius:7px;cursor:pointer;font-size:12px}" +
      ".efc-clima-replay__scrub{width:min(42vw,220px);accent-color:#38bdf8}" +
      ".efc-clima-replay__label{min-width:9.5ch;font-variant-numeric:tabular-nums;color:#cbd5e1}";
    document.head.appendChild(style);
    host.appendChild(bar);
    var play = bar.querySelector(".efc-clima-replay__play");
    var scrub = bar.querySelector(".efc-clima-replay__scrub");
    play.addEventListener("click", function () {
      if (state.playing) pause();
      else playFrames();
    });
    scrub.addEventListener("input", function () {
      pause();
      setFrame(Number(scrub.value) || 0);
    });
    return bar;
  }

  function fmtLabel(t) {
    if (!t) return "—";
    var s = String(t);
    // 2026-11-05T18:00 → 05/11 18h
    var m = s.match(/(\d{4})-(\d{2})-(\d{2})T(\d{2})/);
    if (!m) return s.slice(0, 16);
    return m[3] + "/" + m[2] + " " + m[4] + "h";
  }

  function updateReplayUI() {
    var map = state.map;
    if (!map) return;
    var bar = ensureReplayBar(map);
    if (!state.on || !state.frames.length) {
      bar.setAttribute("hidden", "");
      return;
    }
    bar.removeAttribute("hidden");
    var scrub = bar.querySelector(".efc-clima-replay__scrub");
    var play = bar.querySelector(".efc-clima-replay__play");
    var label = bar.querySelector(".efc-clima-replay__label");
    scrub.max = String(Math.max(0, state.frames.length - 1));
    scrub.value = String(state.frameI);
    label.textContent = fmtLabel((state.frames[state.frameI] || {}).t);
    play.textContent = state.playing ? "❚❚" : "▶";
  }

  function seedParticles(n) {
    var parts = [];
    for (var i = 0; i < n; i++) {
      parts.push({ x: Math.random(), y: Math.random(), life: Math.random() });
    }
    state.particles = parts;
  }

  function nearestWind(lat, lon, frameI) {
    var best = null;
    var bestD = 1e18;
    for (var i = 0; i < state.grid.length; i++) {
      var g = state.grid[i];
      var dlat = g.lat - lat;
      var dlon = g.lon - lon;
      var d = dlat * dlat + dlon * dlon;
      if (d < bestD) {
        bestD = d;
        var u = (g.u || [])[frameI] || 0;
        var v = (g.v || [])[frameI] || 0;
        var cloud = (g.cloud || [])[frameI] || 0;
        var precip = (g.precip || [])[frameI] || 0;
        best = { u: u, v: v, cloud: cloud, precip: precip, ws: Math.sqrt(u * u + v * v) };
      }
    }
    return best || { u: 0, v: 0, cloud: 0, precip: 0, ws: 0 };
  }

  function rainColor(mmh) {
    if (mmh < 0.1) return null;
    if (mmh < 0.5) return "rgba(125,211,252,0.28)";
    if (mmh < 1.5) return "rgba(56,189,248,0.42)";
    if (mmh < 4) return "rgba(37,99,235,0.5)";
    if (mmh < 8) return "rgba(124,58,237,0.55)";
    return "rgba(219,39,119,0.6)";
  }

  function draw() {
    var map = state.map;
    if (!map || !state.on) return;
    var c = ensureCanvas(map);
    var size = map.getSize();
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = Math.round(size.x * dpr);
    c.height = Math.round(size.y * dpr);
    c.style.width = size.x + "px";
    c.style.height = size.y + "px";
    var ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);

    var fi = state.frameI;
    var bounds = map.getBounds();
    var pad = 0.15;

    // Cloud / rain blobs
    for (var i = 0; i < state.grid.length; i++) {
      var g = state.grid[i];
      if (!bounds.pad(pad).contains([g.lat, g.lon])) continue;
      var cloud = (g.cloud || [])[fi] || 0;
      var precip = (g.precip || [])[fi] || 0;
      var pt = map.latLngToContainerPoint([g.lat, g.lon]);
      var rCloud = 38 + cloud * 0.55;
      var alpha = Math.min(0.55, 0.08 + cloud / 180);
      var grd = ctx.createRadialGradient(pt.x, pt.y, 4, pt.x, pt.y, rCloud);
      grd.addColorStop(0, "rgba(226,232,240," + (alpha * 0.95).toFixed(3) + ")");
      grd.addColorStop(0.45, "rgba(148,163,184," + (alpha * 0.55).toFixed(3) + ")");
      grd.addColorStop(1, "rgba(148,163,184,0)");
      ctx.fillStyle = grd;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, rCloud, 0, Math.PI * 2);
      ctx.fill();
      var rc = rainColor(precip);
      if (rc) {
        var rRain = 22 + Math.sqrt(precip) * 18;
        var rg = ctx.createRadialGradient(pt.x, pt.y, 2, pt.x, pt.y, rRain);
        rg.addColorStop(0, rc);
        rg.addColorStop(1, "rgba(37,99,235,0)");
        ctx.fillStyle = rg;
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, rRain, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Wind particles
    if (!state.particles.length) seedParticles(220);
    var b = bounds;
    var west = b.getWest();
    var east = b.getEast();
    var south = b.getSouth();
    var north = b.getNorth();
    var spanLon = Math.max(1e-6, east - west);
    var spanLat = Math.max(1e-6, north - south);

    for (var p = 0; p < state.particles.length; p++) {
      var part = state.particles[p];
      var lon = west + part.x * spanLon;
      var lat = south + part.y * spanLat;
      var w = nearestWind(lat, lon, fi);
      // Advance in normalized map space (speed scaled)
      var speed = Math.max(0.4, Math.min(18, w.ws || 0));
      var scale = 0.00035 * speed;
      part.x += (w.u || 0) * scale / spanLon;
      part.y += (w.v || 0) * scale / spanLat;
      part.life -= 0.004 + speed * 0.0008;
      if (part.x < 0 || part.x > 1 || part.y < 0 || part.y > 1 || part.life <= 0) {
        part.x = Math.random();
        part.y = Math.random();
        part.life = 0.6 + Math.random() * 0.8;
        continue;
      }
      var lon2 = west + part.x * spanLon;
      var lat2 = south + part.y * spanLat;
      var a = map.latLngToContainerPoint([lat, lon]);
      var bpt = map.latLngToContainerPoint([lat2, lon2]);
      var opac = Math.min(0.85, 0.25 + speed / 20);
      ctx.strokeStyle = "rgba(224,242,254," + opac.toFixed(3) + ")";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(bpt.x, bpt.y);
      ctx.stroke();
      // arrow head
      var ang = Math.atan2(bpt.y - a.y, bpt.x - a.x);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.moveTo(bpt.x, bpt.y);
      ctx.lineTo(bpt.x - 5 * Math.cos(ang - 0.4), bpt.y - 5 * Math.sin(ang - 0.4));
      ctx.lineTo(bpt.x - 5 * Math.cos(ang + 0.4), bpt.y - 5 * Math.sin(ang + 0.4));
      ctx.closePath();
      ctx.fill();
    }

    if (state.on) {
      state.raf = requestAnimationFrame(draw);
    }
  }

  function setFrame(i) {
    if (!state.frames.length) return;
    state.frameI = Math.max(0, Math.min(state.frames.length - 1, i | 0));
    updateReplayUI();
  }

  function playFrames() {
    if (!state.frames.length) return;
    state.playing = true;
    updateReplayUI();
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(function () {
      var next = state.frameI + 1;
      if (next >= state.frames.length) next = 0;
      setFrame(next);
    }, 700);
  }

  function pause() {
    state.playing = false;
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    updateReplayUI();
  }

  function clear() {
    pause();
    state.on = false;
    if (state.raf) {
      cancelAnimationFrame(state.raf);
      state.raf = 0;
    }
    if (state.canvas && state.canvas.parentNode) {
      try {
        state.canvas.parentNode.removeChild(state.canvas);
      } catch (e0) {}
    }
    state.canvas = null;
    if (state.map) {
      var bar = state.map.getContainer().querySelector(".efc-clima-replay");
      if (bar) bar.setAttribute("hidden", "");
    }
    state.grid = [];
    state.frames = [];
    state.particles = [];
  }

  function bindMap(map) {
    if (state.bound && state.map === map) return;
    state.map = map;
    state.bound = true;
    map.on("move zoom resize", function () {
      /* redraw via raf */
    });
  }

  function set(on, payload) {
    var map = findMap();
    if (!map) return;
    bindMap(map);
    if (!on) {
      clear();
      return;
    }
    state.on = true;
    payload = payload || {};
    state.grid = payload.grid || [];
    state.frames = payload.frames || [];
    state.frameI = payload.frame_i != null ? payload.frame_i : 0;
    // Legacy points-only → synthesize 1-frame grid
    if (!state.grid.length && payload.points && payload.points.length) {
      state.grid = payload.points.map(function (p) {
        var u = p.u != null ? Number(p.u) : 0;
        var v = p.v != null ? Number(p.v) : 0;
        var precip = p.precip_mm_h != null ? p.precip_mm_h : p.precip_mm != null ? p.precip_mm : 0;
        var cloud = p.cloud_pct != null ? p.cloud_pct : precip > 0 ? 70 : 20;
        var ws = p.wind_ms != null ? Number(p.wind_ms) : Math.sqrt(u * u + v * v);
        return {
          km: p.km,
          lat: p.lat,
          lon: p.lon,
          precip: [Number(precip) || 0],
          cloud: [Number(cloud) || 0],
          ws: [ws],
          u: [u],
          v: [v],
        };
      });
      state.frames = [{ i: 0, t: "agora" }];
      state.frameI = 0;
    }
    ensureCanvas(map);
    seedParticles(240);
    updateReplayUI();
    if (state.raf) cancelAnimationFrame(state.raf);
    draw();
    // Auto-play so wind/rain motion is obvious on toggle
    if (state.frames.length > 1) playFrames();
  }

  global.EfcClimaLayer = {
    set: set,
    clear: clear,
    setFrame: setFrame,
    play: playFrames,
    pause: pause,
  };
})(window);
