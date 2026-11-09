"""Contexto da página Visão Detalhada."""
from __future__ import annotations

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

from django.conf import settings

from lib_efc import (
    THRESHOLD_120H_MM,
    THRESHOLD_120H_WARNING_MM,
    THRESHOLD_24H_MM,
    THRESHOLD_30D_MM,
    THRESHOLD_7D_MM,
    ANA_ADOTADA_CSV,
    ANA_ESTACOES_CSV,
    BNDMET_DADOS_CSV,
    BNDMET_ESTACOES_CSV,
    build_folium_railway_map,
    get_merge_along_railway_stats,
    get_railway_data,
    load_ana_estacoes,
    load_bndmet_estacoes,
    load_orion_estacoes,
    table_exceedance_by_railway_km,
)
from monitor_web.i18n import get_lang
from monitor_web.services.cache_keys import bq_fingerprint, cached_call, map_folium_cache_key
from monitor_web.services.home_impact_enrich import home_impact_for_request
from monitor_web.services.home_indicators_html import indicators_panel_html
from monitor_web.services.html_ui import (
    dashboard_panel_header,
    kpi_card,
    map_help_context,
)
from monitor_web.services.plotly_charts import fig_to_json

COLOR_24H = "#f87171"
COLOR_SINGLE_24H = "#fb923c"


def _ttl() -> int:
    return int(getattr(settings, "MONITOR_BQ_CACHE_TTL", 300))


def _csv_mtime(path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _railway():
    return cached_call("railway_geom_v2", 3600, get_railway_data)


def _era5_bq_snapshot(dataset: str, days: int):
    fp = bq_fingerprint()

    def _load():
        from gcp.bq_reader_clima import load_available_dates_from_bq, load_efc_precip_from_bq, table_exceedance_from_bq

        df_series, df_locations = load_efc_precip_from_bq(dataset=dataset, source="all", days=days)
        if df_locations is None or df_locations.empty:
            return None, None, None, None
        if df_series is None:
            df_series = pd.DataFrame()
        df_exceed = table_exceedance_from_bq(df_series, df_locations, THRESHOLD_24H_MM, THRESHOLD_120H_MM)
        times = load_available_dates_from_bq(dataset=dataset, source="all", days=days)
        return df_exceed, times, df_series, df_locations

    return cached_call(f"era5_bq_{dataset}_{days}_{fp}", _ttl(), _load)


def _home_window(dataset: str, d_start: str, d_end: str):
    fp = bq_fingerprint()

    def _load():
        from gcp.bq_reader_clima import load_efc_precip_from_bq_date_range

        return load_efc_precip_from_bq_date_range(
            dataset=dataset, source="all", date_start=d_start, date_end=d_end
        )

    return cached_call(f"home_win_{dataset}_{d_start}_{d_end}_{fp}", _ttl(), _load)


def folium_map_standalone_html(m, *, height: str = "390px", map3d_payload: dict | None = None) -> str:
    """Documento HTML completo do mapa (iframe embed — Leaflet no mesmo documento)."""
    if not m:
        return ""
    root = m.get_root()
    root.width = "100%"
    root.height = height
    html = root.render()
    legend = getattr(m, "_efc_station_legend_html", "")
    if legend:
        marker = "</body>"
        if marker in html:
            html = html.replace(marker, f"{legend}{marker}", 1)
        else:
            html = html + legend
    import json as _json

    payload = map3d_payload or {}
    payload_js = _json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # Bridge: bolhas acumuladas + Suscetibilidade (pref parent.localStorage / ?susc=).
    # Restaurar no iframe é mais confiável que o parent clicar no LayerControl após HTMX.
    acc_bridge = (
        "<style>.efc-raijin-tp-icon{background:transparent!important;border:0!important;}"
        ".leaflet-pane.efcAlertTpPane{z-index:680!important;}"
        ".leaflet-pane.efcAlertFpPane{z-index:440!important;}"
        ".leaflet-pane.efcAlertDayPane{z-index:450!important;}"
        ".leaflet-pane.efcAccPane path,.leaflet-pane.efcAlertDayPane path{"
        "stroke:#0a0a0a!important;stroke-opacity:1!important;stroke-width:2.6px!important;}"
        ".leaflet-popup-content:has(.efc-alert-card){margin:0!important;width:auto!important;line-height:1.35;}"
        ".leaflet-popup-content-wrapper:has(.efc-alert-card){"
        "background:transparent!important;box-shadow:none!important;padding:0!important;border-radius:10px!important;}"
        ".leaflet-popup:has(.efc-alert-card) .leaflet-popup-tip{background:#141c2b!important;}"
        ".leaflet-popup:has(.efc-alert-card) a.leaflet-popup-close-button{"
        "color:#f8fafc!important;top:6px;right:6px;z-index:2;}"
        ".leaflet-top.leaflet-left .leaflet-control-layers{"
        "margin-top:10px;background:rgba(15,23,42,.92);color:#e2e8f0;"
        "border:1px solid rgba(148,163,184,.35);border-radius:8px;}"
        ".leaflet-control-layers-expanded{padding:8px 10px 8px 8px;}"
        ".leaflet-control-layers label{color:#e2e8f0;}"
        "@media (max-width:480px){"
        ".leaflet-top.leaflet-left .leaflet-control-layers{margin:8px 0 0 8px;}"
        ".leaflet-control-layers-expanded{"
        "max-height:min(42vh,220px);overflow-y:auto;-webkit-overflow-scrolling:touch;"
        "padding:6px 8px;font-size:12px;line-height:1.35;}"
        ".leaflet-control-layers-expanded label{"
        "display:flex;align-items:center;gap:6px;min-height:36px;margin:0;padding:2px 0;}"
        ".leaflet-control-layers-expanded .leaflet-control-layers-selector{margin:0;}"
        ".leaflet-control-layers-separator{margin:4px 0;}"
        "}"
        "</style>"
        '<script>(function(){'
        'var g=null;var SUSC_KEY="efc_map_suscetibilidade_on";'
        'function f(){if(!window.L)return null;'
        'var el=document.querySelector(".folium-map");'
        'if(el&&el.id&&window[el.id]&&typeof window[el.id].addLayer==="function")return window[el.id];'
        'var k;for(k in window){if(k.indexOf("map_")===0&&window[k]&&typeof window[k].addLayer==="function")return window[k];}'
        'return null;}'
        'function panes(m){if(!m.getPane("efcAccPane")){m.createPane("efcAccPane");m.getPane("efcAccPane").style.zIndex=450;}}'
        'function accR(base,z){var b=Math.max(12,base||14);var extra=Math.max(0,(z==null?8:z)-9);return Math.min(52,Math.round(b+extra*3.6));}'
        'function bubbleOpt(x,z){return {radius:accR(x.radius||14,z),color:"#0a0a0a",fillColor:x.color||"#facc15",'
        'fillOpacity:0.8,weight:2.6,opacity:1,pane:"efcAccPane",zIndexOffset:200};}'
        'function scaleBubbles(m){if(!m||!m.eachLayer)return;var z=m.getZoom();'
        'm.eachLayer(function walk(layer){if(layer.eachLayer){layer.eachLayer(walk);return;}'
        'if(!layer.setRadius||!layer.options)return;var pane=layer.options.pane||"";'
        'if(pane!=="efcAccPane"&&pane!=="efcAlertDayPane")return;'
        'if(layer._efcBaseR==null)layer._efcBaseR=layer.options.radius||14;'
        'layer.setRadius(accR(layer._efcBaseR,z));});}'
        'function pop(x){var n=x.n_tp!=null?x.n_tp:(x.n_merge_24!=null?x.n_merge_24:(x.n_alertas||0));'
        'var h="<b>km "+x.km+"</b><br>TP confirmados: "+n+" dia(s)";'
        'return h+"<br>";}'
        'var lastMs=[];'
        'function d(ms){if(ms)lastMs=ms;if(g&&f()){try{f().removeLayer(g);}catch(e){}}g=null;if(!lastMs||!lastMs.length)return;'
        'var L=window.L,m=f();if(!L||!m)return;panes(m);g=L.layerGroup();var z=m.getZoom();'
        'lastMs.forEach(function(x){var c=L.circleMarker([x.lat,x.lon],bubbleOpt(x,z)).bindPopup(pop(x)).addTo(g);c._efcBaseR=x.radius||14;});'
        'g.addTo(m);if(!m._efcAccZoomBound){m._efcAccZoomBound=1;m.on("zoomend",function(){scaleBubbles(m);});}scaleBubbles(m);}'
        'window.addEventListener("message",function(ev){var p=ev.data;if(!p||p.type!=="efc-acc-alerts")return;'
        'd(p.markers||[]);});'
        'function isSuscName(n){n=String(n||"").toLowerCase();return n.indexOf("suscetibilidade")>=0||n.indexOf("susceptibility")>=0;}'
        'function readSuscPref(){try{var q=new URLSearchParams(window.location.search);'
        'if(q.has("susc"))return q.get("susc")==="1"||q.get("susc")==="true";}catch(e0){}'
        'try{if(window.parent&&window.parent!==window)return window.parent.localStorage.getItem(SUSC_KEY)==="1";}'
        'catch(e1){}try{return localStorage.getItem(SUSC_KEY)==="1";}catch(e2){return false;}}'
        'function writeSuscPref(on){try{if(window.parent&&window.parent!==window)'
        'window.parent.localStorage.setItem(SUSC_KEY,on?"1":"0");}catch(e3){}'
        'try{localStorage.setItem(SUSC_KEY,on?"1":"0");}catch(e4){}}'
        'function suscCheckbox(){var labels=document.querySelectorAll('
        '".leaflet-control-layers-overlays label,.leaflet-control-layers label");'
        'for(var i=0;i<labels.length;i++){if(isSuscName(labels[i].textContent))'
        'return labels[i].querySelector(\'input[type="checkbox"]\');}return null;}'
        'function findSuscLayer(m){if(m._efcSuscLayer)return m._efcSuscLayer;'
        'var input=suscCheckbox(),layerId=input&&input.layerId!=null?input.layerId:null,k;'
        'for(k in window){try{var cand=window[k];'
        'if(!cand||cand._map!==m||!Array.isArray(cand._layers))continue;'
        'for(var li=0;li<cand._layers.length;li++){var e=cand._layers[li];'
        'if(!e||!e.overlay||!e.layer)continue;'
        'if(isSuscName(e.name)||(layerId!=null&&window.L&&L.Util.stamp(e.layer)===layerId))'
        '{m._efcSuscLayer=e.layer;return e.layer;}}}catch(eS){}}return null;}'
        'function applySusc(m){var want=readSuscPref(),input=suscCheckbox(),layer=findSuscLayer(m);'
        'if(input&&!input._efcSuscBound){input._efcSuscBound=1;'
        'input.addEventListener("change",function(){writeSuscPref(!!input.checked);});}'
        'if(!m._efcSuscMapBound){m._efcSuscMapBound=1;'
        'm.on("overlayadd",function(ev){if(isSuscName(ev&&ev.name)){if(ev.layer)m._efcSuscLayer=ev.layer;writeSuscPref(true);}});'
        'm.on("overlayremove",function(ev){if(isSuscName(ev&&ev.name)){if(ev.layer)m._efcSuscLayer=ev.layer;writeSuscPref(false);}});}'
        'if(input&&Boolean(input.checked)!==Boolean(want)){try{input.click();}catch(eC){}}'
        'if(layer){try{if(want&&!m.hasLayer(layer))m.addLayer(layer);'
        'if(!want&&m.hasLayer(layer))m.removeLayer(layer);}catch(eL){}}'
        'return !!suscCheckbox();}'
        'function fitRail(m){try{var b=window.__efcRailBounds;if(!m||!b||!b.length)return;'
        'm.fitBounds([[b[0][1],b[0][0]],[b[1][1],b[1][0]]],{padding:[28,28],maxZoom:7,animate:false});}catch(eF){}}'
        'function r(){var m=f();if(m){try{'
        'm.invalidateSize();'
        'if(m.options&&m.options.wheelPxPerZoomLevel)m.scrollWheelZoom&&m.scrollWheelZoom.enable();'
        'if(!m._efcFitted){m._efcFitted=1;fitRail(m);}'
        'applySusc(m);scaleBubbles(m);'
        'if(!m._efcAccZoomBound){m._efcAccZoomBound=1;m.on("zoomend",function(){scaleBubbles(m);});}'
        'window.parent.postMessage({type:"efc-map-ready"},window.location.origin);}catch(e){}return;}'
        'setTimeout(r,150);}r();'
        'var _suscTries=0;function suscRetry(){if(applySusc(f())||_suscTries>40)return;'
        '_suscTries++;setTimeout(suscRetry,_suscTries<10?80:200);}setTimeout(suscRetry,200);'
        # Compact parent strip -> iframe: 3D / sat / clima / susc / ir-para km
        'var climaG=null;'
        'function setSusc(on){writeSuscPref(!!on);var m=f();if(m)applySusc(m);}'
        'var gotoMarker=null;'
        'function showGotoLabel(m,lat,lon,km){if(!m||!window.L||!isFinite(lat)||!isFinite(lon))return;'
        'if(gotoMarker){try{m.removeLayer(gotoMarker);}catch(eGm){}}'
        'var html="<div class=\\"efc-km-goto-label\\">km "+(km!=null?Math.round(km):"?")+'
        '"<small>ferrovia</small></div>";'
        'gotoMarker=L.marker([lat,lon],{icon:L.divIcon({className:"efc-km-goto-marker",html:html,'
        'iconSize:[1,1],iconAnchor:[0,12]}),zIndexOffset:900}).addTo(m);}'
        'function gotoKm(p){var m=f();if(!m||!window.L)return;'
        'var lat=p&&p.lat!=null?Number(p.lat):NaN,lon=p&&p.lon!=null?Number(p.lon):NaN;'
        'var km=p&&p.km!=null?Number(p.km):NaN;'
        'if(isFinite(lat)&&isFinite(lon)){m.setView([lat,lon],Math.max(m.getZoom(),12),{animate:true});'
        'showGotoLabel(m,lat,lon,isFinite(km)?km:null);return;}'
        'if(!isFinite(km))return;var hit=null;'
        'm.eachLayer(function walk(layer){if(layer.eachLayer){layer.eachLayer(walk);return;}'
        'var t=layer.getTooltip&&layer.getTooltip();'
        'var txt=t&&t.getContent?String(t.getContent()):"";'
        'if(txt==="km "+km||txt.indexOf("km "+km)===0){hit=layer;}});'
        'if(hit&&hit.getLatLng){m.setView(hit.getLatLng(),Math.max(m.getZoom(),12),{animate:true});'
        'showGotoLabel(m,hit.getLatLng().lat,hit.getLatLng().lng,km);'
        'try{hit.openPopup&&hit.openPopup();}catch(eG){}}'
        'else{window.parent.postMessage({type:"efc-goto-km-miss",km:km},"*");}}'
        'function setClima(on,payload){'
        'if(window.EfcClimaLayer){window.EfcClimaLayer.set(!!on,payload||{});return;}'
        'var m=f();if(!on){if(climaG&&m){try{m.removeLayer(climaG);}catch(eC0){}}climaG=null;}}'
        'window.addEventListener("message",function(ev){var p=ev.data;if(!p||!p.type)return;'
        'if(p.type==="efc-map-susc")setSusc(!!p.on);'
        'if(p.type==="efc-map-goto-km")gotoKm(p);'
        'if(p.type==="efc-map-clima"){'
        'var climate=p.climate||{points:p.points||[],grid:p.grid||null,frames:p.frames||null,frame_i:p.frame_i};'
        'setClima(!!p.on,climate);}'
        'if(p.type==="efc-map-basemap"){'
        'if(String(p.mode||"")==="sat"){var m2=f();if(m2){m2.invalidateSize();fitRail(m2);}}'
        'if(String(p.mode||"")==="3d"&&window.EfcClimaLayer){try{window.EfcClimaLayer.clear();}catch(eCl){}}'
        '}'
        '});'
        "})();</script>"
        '<script src="/static/monitor/js/map_clima_layer.js?v=20261105speed1"></script>'
        # MapLibre 3D — lazy: só baixa CDN + inicia quando o parent pede mode=3d
        # (Leaflet 2D pinta primeiro; evita 20–40s de tiles DEM/MapLibre no first paint).
        '<div id="efc-maplibre-3d" style="position:absolute;inset:0;z-index:450;display:none;"></div>'
        '<style>.efc-km-goto-marker{background:transparent;border:0;}'
        '.efc-km-goto-label{font:800 15px/1.1 "IBM Plex Sans",sans-serif;color:#fff;'
        'background:rgba(15,23,42,.92);border:2px solid #38bdf8;border-radius:10px;'
        'padding:8px 12px;box-shadow:0 8px 22px rgba(2,6,23,.45);white-space:nowrap;}'
        '.efc-km-goto-label small{display:block;font:650 10px/1.2 "IBM Plex Sans",sans-serif;'
        'color:#94a3b8;margin-top:3px;letter-spacing:.04em;text-transform:uppercase;}</style>'
        '<script>(function(){'
        f"var OVERLAY={payload_js};"
        'window.__efcMap3dOverlay=OVERLAY;window.__efcRailBounds=OVERLAY&&OVERLAY.bounds||null;'
        'var host=document.getElementById("efc-maplibre-3d");if(!host)return;'
        'var map3d=null,loading=null,pendingGoto=null;'
        'function ensureHost(){var foliumEl=document.querySelector(".folium-map");'
        'if(foliumEl){foliumEl.style.position="relative";'
        'if(host.parentNode!==foliumEl){foliumEl.style.height=foliumEl.style.height||"100%";foliumEl.appendChild(host);}}}'
        'function loadScript(src){return new Promise(function(res,rej){'
        'if(window.maplibregl){res();return;}var s=document.createElement("script");s.src=src;s.async=true;'
        's.onload=function(){res();};s.onerror=rej;document.head.appendChild(s);});}'
        'function loadCss(href){if(document.querySelector(\'link[data-efc-maplibre="1"]\'))return;'
        'var l=document.createElement("link");l.rel="stylesheet";l.href=href;l.setAttribute("data-efc-maplibre","1");'
        'document.head.appendChild(l);}'
        'function addOverlay(m){if(!OVERLAY||!m)return;'
        'if(OVERLAY.rail){m.addSource("efc-rail",{type:"geojson",data:OVERLAY.rail});'
        'm.addLayer({id:"efc-rail-casing",type:"line",source:"efc-rail",paint:{'
        '"line-color":"#0b1220","line-width":7,"line-opacity":0.92,"line-blur":0.2}});'
        'm.addLayer({id:"efc-rail-ballast",type:"line",source:"efc-rail",paint:{'
        '"line-color":"#94a3b8","line-width":3.4,"line-opacity":0.95}});'
        'm.addLayer({id:"efc-rail-steel",type:"line",source:"efc-rail",paint:{'
        '"line-color":"#f8fafc","line-width":1.25,"line-opacity":1}});}'
        'if(OVERLAY.kmLabels){m.addSource("efc-km-labels",{type:"geojson",data:OVERLAY.kmLabels});'
        'm.addLayer({id:"efc-km-halo",type:"symbol",source:"efc-km-labels",layout:{'
        '"text-field":["concat","km ",["to-string",["get","km"]]],"text-size":12,'
        '"text-font":["Open Sans Bold","Arial Unicode MS Bold"],'
        '"text-offset":[0,-0.9],"text-anchor":"bottom","text-allow-overlap":true,'
        '"symbol-placement":"point"},paint:{'
        '"text-color":"#f8fafc","text-halo-color":"#0b1220","text-halo-width":1.6,"text-opacity":0.95}});}'
        'if(OVERLAY.tunnels){m.addSource("efc-tunnels",{type:"geojson",data:OVERLAY.tunnels});'
        'm.addLayer({id:"efc-tunnel-line",type:"line",source:"efc-tunnels",'
        'filter:["==",["get","kind"],"tunnel"],paint:{'
        '"line-color":"#f59e0b","line-width":6,"line-opacity":0.92,"line-dasharray":[1.2,1.1]}});'
        'm.addLayer({id:"efc-tunnel-portal",type:"circle",source:"efc-tunnels",'
        'filter:["==",["get","kind"],"portal"],paint:{'
        '"circle-radius":7,"circle-color":"#78350f","circle-stroke-width":2,"circle-stroke-color":"#fbbf24"}});}'
        'if(OVERLAY.stations){m.addSource("efc-stations",{type:"geojson",data:OVERLAY.stations});'
        'm.addLayer({id:"efc-stations",type:"circle",source:"efc-stations",paint:{'
        '"circle-radius":5.5,"circle-color":["get","color"],"circle-stroke-width":1.4,'
        '"circle-stroke-color":"#0f172a","circle-opacity":0.95}});'
        'm.on("click","efc-stations",function(e){var f=e.features&&e.features[0];if(!f)return;'
        'var p=f.properties||{};new maplibregl.Popup({offset:10}).setLngLat(e.lngLat)'
        '.setHTML("<b>"+(p.name||p.code||"")+"</b><br>"+(p.net||"")+(p.code?" · "+p.code:"")).addTo(m);});'
        'm.on("mouseenter","efc-stations",function(){m.getCanvas().style.cursor="pointer";});'
        'm.on("mouseleave","efc-stations",function(){m.getCanvas().style.cursor="";});}}'
        'function cinematicEntry(m){'
        'if(OVERLAY&&OVERLAY.camera){var cam=OVERLAY.camera;'
        'm.jumpTo({center:[cam.lon,cam.lat],zoom:cam.zoom||6.4,pitch:cam.pitch||70,bearing:cam.bearing||-38});return;}'
        'if(OVERLAY&&OVERLAY.bounds){try{m.fitBounds(OVERLAY.bounds,{padding:{top:36,bottom:72,left:48,right:48},'
        'pitch:70,bearing:-38,duration:0,maxZoom:6.6});}catch(eB){}}}'
        'function showGotoKm(p){if(!map3d||!p||p.lat==null||p.lon==null){pendingGoto=p;return;}'
        'var lon=Number(p.lon),lat=Number(p.lat),km=p.km!=null?Math.round(Number(p.km)):null;'
        'map3d.easeTo({center:[lon,lat],zoom:12.6,pitch:66,bearing:-28,duration:950});'
        'if(window.__efcKmGotoMarker){try{window.__efcKmGotoMarker.remove();}catch(eM){}}'
        'var el=document.createElement("div");el.className="efc-km-goto-label";'
        'el.innerHTML=(km!=null?("km "+km):"EFC")+"<small>ferrovia</small>";'
        'window.__efcKmGotoMarker=new maplibregl.Marker({element:el,anchor:"bottom",offset:[0,-10]})'
        '.setLngLat([lon,lat]).addTo(map3d);}'
        'function init3d(){if(map3d){host.style.display="block";try{map3d.resize();}catch(eR){}return Promise.resolve(map3d);}'
        'if(loading)return loading;ensureHost();host.style.display="block";'
        'loadCss("https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css");'
        'loading=loadScript("https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js").then(function(){'
        'var satTiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";'
        'var demTiles="https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";'
        'var style={version:8,sources:{esri:{type:"raster",tiles:[satTiles],tileSize:256,maxzoom:17,'
        'attribution:"Esri · ~2 m"},terrainSource:{type:"raster-dem",tiles:[demTiles],tileSize:256,'
        'maxzoom:15,encoding:"terrarium"}},layers:[{id:"sat",type:"raster",source:"esri"}],'
        'terrain:{source:"terrainSource",exaggeration:2.85}};'
        'var c={lat:-5.05,lon:-48.35,zoom:6.15};'
        'try{for(var k in window){if(k.indexOf("map_")==0&&window[k]&&window[k].getCenter){'
        'var cc=window[k].getCenter();c.lat=cc.lat;c.lon=cc.lng;c.zoom=window[k].getZoom()||6.15;break;}}}catch(e0){}'
        'map3d=new maplibregl.Map({container:host,style:style,center:[c.lon,c.lat],zoom:Math.max(5.6,c.zoom-0.15),'
        'pitch:68,bearing:-32,maxPitch:80,attributionControl:false});'
        'map3d.addControl(new maplibregl.NavigationControl({visualizePitch:true}),"top-right");'
        'window.__efcMap3d=map3d;window.__efcMap3dHost=host;window.__efcKmGotoMarker=null;'
        'map3d.on("load",function(){addOverlay(map3d);cinematicEntry(map3d);'
        'if(pendingGoto){showGotoKm(pendingGoto);pendingGoto=null;}'
        'window.parent.postMessage({type:"efc-map-3d-ready"},"*");});'
        'return map3d;}).catch(function(err){loading=null;host.style.display="none";throw err;});'
        'return loading;}'
        'window.addEventListener("message",function(ev){var p=ev.data;if(!p||!p.type)return;'
        'if(p.type==="efc-map-basemap"){'
        'var want3d=String(p.mode||"")==="3d";'
        'if(want3d){init3d();}else{host.style.display="none";}'
        '}'
        'if(p.type==="efc-map-goto-km"){if(map3d)showGotoKm(p);else pendingGoto=p;}'
        '});'
        '})();</script>'
    )
    marker = "</body>"
    if marker in html:
        html = html.replace(marker, acc_bridge + marker, 1)
    else:
        html = html + acc_bridge
    return html


def folium_map_inline_html(m, *, height: str = "390px") -> str:
    """HTML Folium inline (sem iframe srcdoc do Jupyter)."""
    if not m:
        return ""
    root = m.get_root()
    root.width = "100%"
    root.height = height
    legend = getattr(m, "_efc_station_legend_html", "")
    return f'<div class="efc-map-wrap">{root.render()}{legend}</div>'


def _map_unavailable_message(lang_pt: bool, *, hint: str | None = None, folium_missing: bool = False) -> str:
    if folium_missing:
        return (
            "Mapa indisponível: instale folium (pip install folium)."
            if lang_pt
            else "Map unavailable: install folium (pip install folium)."
        )
    if hint and getattr(settings, "DEBUG", False):
        return f"Mapa indisponível: {hint}" if lang_pt else f"Map unavailable: {hint}"
    return "Mapa indisponível." if lang_pt else "Map unavailable."


def _map_load_timeout_message(lang_pt: bool) -> str:
    if lang_pt:
        return (
            "O mapa demorou demais para carregar (consultas BigQuery ou geração do mapa). "
            "Atualize a página ou tente outra data."
        )
    return (
        "The map took too long to load (BigQuery or map rendering). "
        "Refresh the page or try another date."
    )


def _map_suscetibilidade_enabled() -> bool:
    """Raster RF no mapa (desligar com SKIP_SUSCETIBILIDADE=1)."""
    return os.environ.get("SKIP_SUSCETIBILIDADE", "").strip().lower() not in ("1", "true", "yes")


def _cached_station_catalog(kind: str, dataset: str):
    """Catálogos BNDMET/ANA/Orion — TTL longo (mudam raramente)."""
    fp = bq_fingerprint()
    ttl = int(os.environ.get("MONITOR_STATION_CATALOG_TTL", "3600") or "3600")
    key = f"station_catalog_v1_{kind}_{dataset}_{fp}"

    def _load():
        if kind == "bndmet":
            return load_bndmet_estacoes(bq_dataset=dataset)
        if kind == "ana":
            return load_ana_estacoes(bq_dataset=dataset)
        if kind == "orion":
            return load_orion_estacoes(bq_dataset=dataset)
        return None

    return cached_call(key, ttl, _load)


def _cached_station_accum(kind: str, ref_date: str, hours: int, dataset: str):
    """Acumulados por rede/janela — reutilizados por mapa + painel de indicadores."""
    from lib_efc import (
        get_ana_accum_hours_stale,
        get_bndmet_accum_hours_stale,
        get_orion_accum_hours_stale,
    )

    fp = bq_fingerprint()
    day = str(ref_date)[:10]
    h = int(hours)
    ttl = int(os.environ.get("MONITOR_STATION_ACCUM_TTL", "900") or "900")
    key = f"station_accum_v1_{kind}_{dataset}_{day}_{h}_{fp}"

    def _load():
        if kind == "bndmet":
            return get_bndmet_accum_hours_stale(day, hours=h, bq_dataset=dataset)
        if kind == "ana":
            return get_ana_accum_hours_stale(day, hours=h, bq_dataset=dataset)
        if kind == "orion":
            return get_orion_accum_hours_stale(day, hours=h, bq_dataset=dataset)
        return {}, set(), {}

    return cached_call(key, ttl, _load)


def _cached_station_accum_map_fast(kind: str, ref_date: str, hours: int, dataset: str):
    """Acumulado leve só via clima_series (1 BQ/rede) — first paint do mapa.

    Evita o caminho horário+CSV+merge (2–3 BQ por rede) que somava 20–40s.
    """
    from lib_efc import _station_accum_stale_from_clima_series

    fp = bq_fingerprint()
    day = str(ref_date)[:10]
    h = int(hours)
    ttl = int(os.environ.get("MONITOR_STATION_ACCUM_TTL", "900") or "900")
    key = f"station_accum_mapfast_v1_{kind}_{dataset}_{day}_{h}_{fp}"
    sources = {
        "bndmet": ("estacoes", "bndmet"),
        "ana": ("ana",),
        "orion": ("orion",),
    }.get(kind, (kind,))

    def _load():
        return _station_accum_stale_from_clima_series(
            day, hours=h, sources=sources, dataset=dataset
        )

    return cached_call(key, ttl, _load)


def _operational_map_html(
    ref_date: str,
    dataset: str,
    pts_24,
    pts_120,
    accum_hours: int = 120,
    km_point_step_override: int = 5,
    embed_document: bool = False,
    alert_markers: tuple = (),
    lang_pt: bool = True,
    show_suscetibilidade: bool = False,
):
    import concurrent.futures

    main_line, cum_km, markers_100, all_lines = _railway()
    if main_line is None:
        return ""
    from monitor_web.services.accum_window import parse_accum_hours

    def _merge_acc(a: dict, b: dict) -> dict:
        out = dict(a or {})
        for k, v in (b or {}).items():
            try:
                out[k] = max(float(out.get(k) or 0.0), float(v or 0.0))
            except (TypeError, ValueError):
                out[k] = v
        return out

    hours = parse_accum_hours(accum_hours)
    # Popup: 24h + 5d; cor segue janela ativa. Path leve (clima_series only).
    need_hours = {24, 120, int(hours)}
    map_fast = os.environ.get("MONITOR_MAP_ACCUM_FAST", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    accum_fn = _cached_station_accum_map_fast if map_fast else _cached_station_accum

    # Catálogos + acumulados em paralelo (antes: 10–15 BQ seriais → 20–40s).
    jobs: dict[str, concurrent.futures.Future] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs["bndmet_cat"] = pool.submit(_cached_station_catalog, "bndmet", dataset)
        jobs["ana_cat"] = pool.submit(_cached_station_catalog, "ana", dataset)
        jobs["orion_cat"] = pool.submit(_cached_station_catalog, "orion", dataset)
        for h in sorted(need_hours):
            for kind in ("bndmet", "orion", "ana"):
                jobs[f"{kind}_{h}"] = pool.submit(accum_fn, kind, ref_date, h, dataset)
        results = {k: f.result() for k, f in jobs.items()}

    estacoes = results["bndmet_cat"]
    ana_est = results["ana_cat"]
    orion_est = results["orion_cat"]
    # Orion/EWS no mesmo layer de marcadores (códigos EWS*/BS*/BC*), acumulado à parte.
    if orion_est is not None and not getattr(orion_est, "empty", True):
        import pandas as _pd

        estacoes = (
            _pd.concat([estacoes, orion_est], ignore_index=True)
            if estacoes is not None and not getattr(estacoes, "empty", True)
            else orion_est
        )

    def _triple(kind: str, h: int):
        return results.get(f"{kind}_{h}") or ({}, set(), {})

    acc24_b, _, _ = _triple("bndmet", 24)
    acc24_o, _, _ = _triple("orion", 24)
    acc24 = _merge_acc(acc24_b, acc24_o)
    acc120_b, st_stale, last_obs = _triple("bndmet", 120)
    acc120_o, st_o, lo_o = _triple("orion", 120)
    acc120 = _merge_acc(acc120_b, acc120_o)
    st_stale = set(st_stale or {}) | set(st_o or {})
    last_obs = {**(last_obs or {}), **(lo_o or {})}
    if hours == 24:
        acc = acc24
    elif hours == 120:
        acc = acc120
    else:
        acc_b, st_stale, last_obs = _triple("bndmet", hours)
        acc_o, st_o, lo_o = _triple("orion", hours)
        acc = _merge_acc(acc_b, acc_o)
        st_stale = set(st_stale or {}) | set(st_o or {})
        last_obs = {**(last_obs or {}), **(lo_o or {})}
    ana_acc24, _, _ = _triple("ana", 24)
    ana_acc120, ana_st, ana_lo = _triple("ana", 120)
    if hours == 24:
        ana_acc = ana_acc24
    elif hours == 120:
        ana_acc = ana_acc120
    else:
        ana_acc, ana_st, ana_lo = _triple("ana", hours)
    m = build_folium_railway_map(
        main_line,
        all_lines or [],
        cum_km,
        np.array([0.0]),
        np.array([0.0]),
        np.zeros((1, 1)),
        THRESHOLD_24H_MM,
        COLOR_24H,
        "24h > 49 mm",
        ref_date,
        color_single_hex=COLOR_SINGLE_24H,
        estacoes_df=estacoes,
        ana_estacoes_df=ana_est,
        points_above_override=list(pts_24 or []),
        points_almost_120h_override=list(pts_120 or []),
        # Camada só com dados pesados se ?susc=1; senão FeatureGroup vazio no control.
        add_suscetibilidade=_map_suscetibilidade_enabled(),
        show_suscetibilidade=bool(show_suscetibilidade),
        markers_100=markers_100,
        show_km_zoom_markers=False,
        show_clickable_km_points=True,
        # Controlo compacto no parent (home_map) — sem Folium LayerControl (OSM/km removidos).
        show_layer_control=False,
        draw_railway_polyline=True,
        railway_line_color="#cbd5e1",
        railway_line_weight=2.5,
        fit_main_line_bounds=True,
        km_point_step_override=km_point_step_override,
        station_accum_120h=acc,
        station_stale=st_stale,
        station_last_obs=last_obs,
        station_accum_24h=acc24,
        station_accum_120h_popup=acc120,
        ana_station_accum_120h=ana_acc,
        ana_station_stale=ana_st,
        ana_station_last_obs=ana_lo,
        ana_station_accum_24h=ana_acc24,
        ana_station_accum_120h_popup=ana_acc120,
        station_accum_hours=hours,
    )
    if not m:
        return ""
    if alert_markers:
        from monitor_web.services.map_alert_markers import add_alert_exclamation_markers
        from monitor_web.services.suscetibilidade import cached_rail_segment_lookup

        add_alert_exclamation_markers(
            m,
            alert_markers,
            main_line,
            cum_km,
            lang_pt=lang_pt,
            segment_lookup=cached_rail_segment_lookup(dataset),
            ref_date=ref_date,
        )
    if embed_document:
        from monitor_web.services.map_3d_overlay import build_map_3d_payload

        overlay = build_map_3d_payload(
            main_line=main_line,
            cum_km=cum_km,
            estacoes_df=estacoes,
            ana_estacoes_df=ana_est,
            orion_estacoes_df=orion_est,
        )
        return folium_map_standalone_html(m, height="420px", map3d_payload=overlay)
    return folium_map_inline_html(m, height="420px")


def build_home_shell_context(request) -> dict:
    """Shell leve: sem BQ pesado (sem snapshot 45d, sem mapa/gráficos)."""
    from monitor_web.services.bq_cache import get_bq_date_range_cached
    from monitor_web.services.home_fragments import bq_locations_probe, parse_home_params

    p = parse_home_params(request)
    lang_pt = p["lang_pt"]
    dataset = p["dataset"]
    chosen = p["chosen"]
    today = date.today()
    use_bq = bq_locations_probe(dataset)

    from monitor_web.services.home_accumulated_alerts import period_controls_context

    ctx = {
        "lang_pt": lang_pt,
        "use_bq": use_bq,
        "dataset": dataset,
        "selected_date": chosen.isoformat(),
        "selected_display": chosen.strftime("%d/%m/%Y"),
        "date_min": (today - timedelta(days=365)).isoformat(),
        "date_max": today.isoformat(),
        "accum_hours": p["accum_hours"],
        "km_max": p["km_max"],
        "fc_km": p["fc_km"],
        "bq_warning": None,
        "home_progressive": True,
        **map_help_context(
            lang_pt=lang_pt,
            selected_display=chosen.strftime("%d/%m/%Y"),
            accum_hours=p["accum_hours"],
            use_bq=use_bq,
        ),
    }
    if use_bq:
        bq_lo, bq_hi = get_bq_date_range_cached(dataset=dataset)
        if bq_lo and bq_hi:
            ctx["date_min"], ctx["date_max"] = bq_lo, bq_hi
            cal_min, cal_max = pd.Timestamp(bq_lo).date(), pd.Timestamp(bq_hi).date()
            if chosen < cal_min:
                chosen = cal_min
            if chosen > cal_max:
                chosen = cal_max
            ctx["selected_date"] = chosen.isoformat()
            ctx["selected_display"] = chosen.strftime("%d/%m/%Y")
        ctx.update(period_controls_context(request, date_max=ctx.get("date_max")))
    else:
        from monitor_web.services.bq_status import bq_warning_message

        ctx["bq_warning"] = bq_warning_message(lang_pt=lang_pt, probe_failed=True)
        ctx.update(period_controls_context(request, date_max=ctx.get("date_max")))

    from monitor_web.forms import HomeToolbarForm

    ctx["toolbar_form"] = HomeToolbarForm(
        initial={"date": chosen, "accum": str(p["accum_hours"])},
        lang_pt=lang_pt,
        date_min=ctx.get("date_min"),
        date_max=ctx.get("date_max"),
    )
    return ctx


def build_home_context(request) -> dict:
    lang_pt = get_lang(request) == "pt"
    try:
        from gcp.monitor_bq_context import monitor_active_bq_dataset

        dataset = monitor_active_bq_dataset()
    except Exception:
        dataset = os.environ.get("BQ_DATASET", "efc_clima")

    main_line, cum_km, _, _ = _railway()
    km_max = int(np.ceil(float(cum_km[-1]))) if cum_km is not None and len(cum_km) else 900
    from monitor_web.services.home_fragments import (
        MAP_CACHE_VER,
        bq_locations_probe,
        load_day_bq,
        load_exceed_window_bq,
        parse_fc_km,
    )

    home_window = int(os.environ.get("HOME_BQ_WINDOW_DAYS", "7"))
    use_bq = bq_locations_probe(dataset)
    today = date.today()

    selected = request.GET.get("date") or today.isoformat()
    try:
        chosen = pd.Timestamp(selected).date()
    except Exception:
        chosen = today

    from monitor_web.services.accum_window import parse_accum_hours

    accum_hours = parse_accum_hours(request.GET.get("accum"))

    from monitor_web.services.home_accumulated_alerts import period_controls_context

    ctx = {
        "lang_pt": lang_pt,
        "use_bq": use_bq,
        "dataset": dataset,
        "selected_date": chosen.isoformat(),
        "selected_display": chosen.strftime("%d/%m/%Y"),
        "status_normal": True,
        "n_crit_day": 0,
        "map_html": "",
        "map_error": None,
        "alert_cards_html": "",
        "kpi_html": "",
        "indicators_html": "",
        "corridor_html": "",
        "heatmap_json": None,
        "km_chart_json": None,
        "forecast_chart_json": None,
        "exceed_table": [],
        "date_min": (today - timedelta(days=365)).isoformat(),
        "date_max": today.isoformat(),
        "accum_hours": accum_hours,
        "km_max": km_max,
        **map_help_context(
            lang_pt=lang_pt,
            selected_display=chosen.strftime("%d/%m/%Y"),
            accum_hours=accum_hours,
            use_bq=use_bq,
        ),
        "fc_km": 379,
        "bq_warning": None,
    }

    if not use_bq:
        from monitor_web.services.bq_status import bq_warning_message

        ctx["bq_warning"] = bq_warning_message(lang_pt=lang_pt, probe_failed=True)
        return ctx

    from gcp.bq_reader_clima import (
        points_above_from_bq_for_date,
        points_almost_120h_from_bq_for_date,
        table_exceedance_operational_from_bq,
    )
    from monitor_web.services.bq_cache import get_bq_date_range_cached

    bq_lo, bq_hi = get_bq_date_range_cached(dataset=dataset)
    if bq_lo and bq_hi:
        ctx["date_min"], ctx["date_max"] = bq_lo, bq_hi
        cal_min, cal_max = pd.Timestamp(bq_lo).date(), pd.Timestamp(bq_hi).date()
        if chosen < cal_min:
            chosen = cal_min
        if chosen > cal_max:
            chosen = cal_max
        ctx["selected_date"] = chosen.isoformat()
        ctx["selected_display"] = chosen.strftime("%d/%m/%Y")
    ctx.update(period_controls_context(request, date_max=ctx.get("date_max")))

    d_end = chosen
    d_start = chosen - timedelta(days=home_window)
    if bq_lo and pd.Timestamp(bq_lo).date() > d_start:
        d_start = pd.Timestamp(bq_lo).date()

    sel = chosen.isoformat()
    ctx["fc_km"] = parse_fc_km(request.GET.get("km"), default=379, km_max=km_max)

    win_start = (pd.Timestamp(sel) - pd.Timedelta(days=29)).date()
    if d_start > win_start:
        win_start = d_start
    if home_window <= 1:
        bq_series, bq_locs = load_exceed_window_bq(dataset, sel)
    else:
        bq_series, bq_locs = _home_window(dataset, win_start.isoformat(), d_end.isoformat())
    bq_day, _ = load_day_bq(dataset, sel)
    if bq_locs is None:
        ctx["use_bq"] = False
        ctx["bq_warning"] = "Sem localizações no dataset."
        return ctx
    if bq_series is None:
        bq_series = pd.DataFrame()
    if bq_day is None:
        bq_day = pd.DataFrame()

    df_day = table_exceedance_operational_from_bq(
        bq_series,
        bq_locs,
        sel,
        THRESHOLD_24H_MM,
        THRESHOLD_120H_MM,
        THRESHOLD_7D_MM,
        THRESHOLD_30D_MM,
    )
    ctx["n_crit_day"] = len(df_day) if df_day is not None and not df_day.empty else 0
    ctx["status_normal"] = ctx["n_crit_day"] == 0

    pts_24 = points_above_from_bq_for_date(bq_day, bq_locs, sel, "precip_24h_mm", THRESHOLD_24H_MM)
    pts_120 = points_almost_120h_from_bq_for_date(
        bq_day, bq_locs, sel, THRESHOLD_120H_WARNING_MM, THRESHOLD_120H_MM
    )

    light = (os.environ.get("HOME_DJANGO_LIGHT") or "").strip().lower() in ("1", "true", "yes")

    ctx["panel_operational"] = dashboard_panel_header(
        "Painel operacional" if lang_pt else "Operations panel", subtitle=""
    )
    ctx["panel_indicators"] = dashboard_panel_header(
        "Indicadores" if lang_pt else "Indicators",
        subtitle=(
            "Severidade · faixas de chuva · limiar 49 mm · observações"
            if lang_pt
            else "Severity · rain bands · 49 mm threshold · observations"
        ),
    )

    if main_line is None:
        ctx["map_error"] = _map_unavailable_message(
            lang_pt, hint="geometria da ferrovia indisponível" if lang_pt else "railway geometry unavailable"
        )
    else:
        try:
            from monitor_web.services.home_fragments import (
                MAP_CACHE_VER,
                alerts_source_for_request,
                day_exceed_for_request,
            )
            from monitor_web.services.map_alert_markers import (
                alert_markers_from_df,
                operational_day_alert_markers_from_df,
            )
            from monitor_web.services.suscetibilidade import count_unique_alert_kms

            # Alinha mapa e painel à mesma fonte (Raijin staff / MERGE portal).
            df_day_src, _, _, _, pts_src = day_exceed_for_request(request, dataset, sel, light=False)
            if df_day_src is not None:
                df_day = df_day_src
            if pts_src:
                pts_24, pts_120 = pts_src
            src = alerts_source_for_request(request)
            if src == "raijin":
                alert_mk = alert_markers_from_df(df_day, raijin_only=True, tp_only=True)
                pts_24, pts_120 = (), ()
            else:
                alert_mk = operational_day_alert_markers_from_df(df_day)
            ctx["n_crit_day"] = count_unique_alert_kms(df_day)
            ctx["status_normal"] = ctx["n_crit_day"] == 0

            map_html = cached_call(
                map_folium_cache_key(
                    ver=MAP_CACHE_VER,
                    sel=sel,
                    dataset=dataset,
                    accum_hours=accum_hours,
                    km_step=5,
                    cache_tag="inline_susc_wide",
                    alert_mk=alert_mk,
                    csv_mtimes=tuple(
                        _csv_mtime(p)
                        for p in (BNDMET_ESTACOES_CSV, BNDMET_DADOS_CSV, ANA_ESTACOES_CSV, ANA_ADOTADA_CSV)
                    ),
                ),
                int(os.environ.get("MONITOR_MAP_CACHE_TTL", "300")),
                _operational_map_html,
                sel,
                dataset,
                pts_24,
                pts_120,
                accum_hours,
                5,
                False,
                alert_mk,
                lang_pt,
                False,
            )
            if map_html:
                ctx["map_html"] = map_html
            else:
                try:
                    import folium  # noqa: F401
                except ImportError:
                    ctx["map_error"] = _map_unavailable_message(lang_pt, folium_missing=True)
                else:
                    ctx["map_error"] = _map_unavailable_message(
                        lang_pt, hint="build_folium_railway_map retornou vazio"
                    )
        except Exception as exc:
            ctx["map_error"] = _map_unavailable_message(lang_pt, hint=str(exc))

    from monitor_web.services.suscetibilidade import (
        build_alert_cards_html,
        cached_rail_segment_lookup,
        exceed_susc_stats_by_kind,
    )

    segment_lookup = cached_rail_segment_lookup(dataset)
    exceed_susc = exceed_susc_stats_by_kind(df_day, segment_lookup)
    ctx["alert_cards_html"] = build_alert_cards_html(
        df_day, lang_pt=lang_pt, lookup=segment_lookup, sel_fallback=sel
    )

    stats_local = get_merge_along_railway_stats(ref_date=sel)
    main_line, cum_km, _, _ = _railway()
    impact = home_impact_for_request(
        request,
        dataset=dataset,
        sel=sel,
        lang_pt=lang_pt,
        main_line=main_line,
        cum_km=cum_km,
        ttl=_ttl(),
    )
    ctx["indicators_html"] = indicators_panel_html(
        lang_pt=lang_pt,
        impact=impact,
        th24=THRESHOLD_24H_MM,
        th120=THRESHOLD_120H_MM,
        th7=THRESHOLD_7D_MM,
        th30=THRESHOLD_30D_MM,
        segment_lookup=segment_lookup,
        exceed_susc_stats=exceed_susc,
    )

    if not impact.get("available"):
        from gcp.bq_reader_clima import count_precip_24h_cells_above_for_date

        n24 = count_precip_24h_cells_above_for_date(bq_series, sel, THRESHOLD_24H_MM)
        total = len(bq_locs) if bq_locs is not None else 0
        pct24 = 100.0 * n24 / total if total else 0
        ctx["kpi_html"] = (
            kpi_card(f"{pct24:.0f}%", "24 h > 49 mm", f"{n24} cél.", color_slot="warning" if pct24 else "normal", compact=True)
            + kpi_card(
                f"{(stats_local or {}).get('max24_val') or '—'}",
                "MERGE 24h",
                f"km {(stats_local or {}).get('max24_km') or '—'}",
                compact=True,
            )
        )

    if df_day is not None and not df_day.empty:
        from monitor_web.services.home_fragments import exceed_table_from_day_df

        ctx["exceed_table"] = exceed_table_from_day_df(
            df_day, dia_label=ctx.get("selected_display")
        )
    else:
        ctx["exceed_table"] = []

    if light:
        return ctx

    # Gráficos por km
    try:
        from gcp.bq_reader_clima import (
            forecast_comparison_series_bq,
            km_exceedance_count_timeseries_bq,
            merge_6h_series_for_km_bq,
            merge_daily_obs_series_for_km_bq,
        )
        import plotly.graph_objects as go
        from monitor_web.services.previsao import _build_forecast_fig

        d_ex, n24, n120 = km_exceedance_count_timeseries_bq(dataset=dataset, days=7)
        if d_ex:
            fb = go.Figure()
            fb.add_trace(go.Bar(x=d_ex, y=n24, name="24h > 49", marker_color="rgba(251,146,60,0.9)"))
            fb.add_trace(go.Bar(x=d_ex, y=n120, name="5 D > 250", marker_color="rgba(129,140,248,0.9)"))
            fb.update_layout(barmode="group", height=280, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            ctx["km_chart_json"] = fig_to_json(fb)

        fc_km = max(0, min(km_max, int(ctx["fc_km"])))
        past_fc, future_fc = 10, 15
        fc_data = forecast_comparison_series_bq(
            float(fc_km), dataset=dataset, past_days=past_fc, future_days=future_fc
        )
        ts_m6, val_m6 = merge_6h_series_for_km_bq(
            float(fc_km), dataset=dataset, past_days=past_fc, future_days=future_fc
        )
        ts_md, val_md = merge_daily_obs_series_for_km_bq(
            float(fc_km), dataset=dataset, past_days=past_fc, future_days=future_fc
        )
        fig_payload = _build_forecast_fig(
            fc_km,
            fc_data,
            ts_m6,
            val_m6,
            ts_md,
            val_md,
            past_days=past_fc,
            future_days=future_fc,
            obs_source="merge",
        )
        if fig_payload:
            import json

            ctx["forecast_chart_json"] = json.dumps(fig_payload)
    except Exception:
        pass

    # Heatmap concordância
    try:
        from gcp.bq_reader_clima import get_model_pr_agreement_railway_heatmap_from_bq
        import plotly.graph_objects as go

        from monitor_web.services.previsao import HEATMAP_COLORSCALE

        kms, day_lbl, z_mm, dates_iso, _, _z_ag = get_model_pr_agreement_railway_heatmap_from_bq(dataset=dataset, horizon_days=7)
        if kms is not None and len(kms) and z_mm.size:
            pr_fig = go.Figure(
                data=go.Heatmap(
                    x=kms,
                    y=[str(x) for x in (dates_iso or day_lbl)],
                    z=z_mm.tolist(),
                    colorscale=HEATMAP_COLORSCALE,
                )
            )
            pr_fig.update_layout(height=380, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            ctx["heatmap_json"] = fig_to_json(pr_fig)
    except Exception:
        pass

    return ctx
