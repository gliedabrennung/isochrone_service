(function () {
  "use strict";

  var CONFIG = Object.assign(
    {
      apiBase: "/api/v1",
      basemapTileUrl: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      basemapAttribution: "&copy; OpenStreetMap contributors",
      maxContours: 4,
      minContourMinutes: 5,
      maxContourMinutes: 60
    },
    window.APP_CONFIG || {}
  );

  var CONTOUR_OPTIONS = [5, 10, 15, 20, 30, 45, 60];
  var DEFAULT_CENTER = { lat: 43.238949, lon: 76.889709 };
  var DEFAULT_ZOOM = 12;
  var SOURCE_ID = "isochrones";

  var state = {
    lat: DEFAULT_CENTER.lat,
    lon: DEFAULT_CENTER.lon,
    mode: "pedestrian",
    contours: [10, 20, 30],
    rings: false,
    excludeWater: true,
    generalize: null,
    zoom: DEFAULT_ZOOM,
    center: [DEFAULT_CENTER.lon, DEFAULT_CENTER.lat]
  };

  var lastResponse = null;
  var pending = false;

  var dom = {
    lat: document.getElementById("lat"),
    lon: document.getElementById("lon"),
    mode: document.getElementById("mode"),
    contours: document.getElementById("contours"),
    contoursCounter: document.getElementById("contours-counter"),
    rings: document.getElementById("rings"),
    excludeWater: document.getElementById("exclude-water"),
    generalize: document.getElementById("generalize"),
    generalizeValue: document.getElementById("generalize-value"),
    generalizeAuto: document.getElementById("generalize-auto"),
    build: document.getElementById("build"),
    error: document.getElementById("error"),
    errorTitle: document.getElementById("error-title"),
    errorDetail: document.getElementById("error-detail"),
    errorMeta: document.getElementById("error-meta"),
    results: document.getElementById("results"),
    legend: document.getElementById("legend"),
    stats: document.getElementById("stats"),
    download: document.getElementById("download"),
    mapHint: document.getElementById("map-hint")
  };

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function parseHash() {
    var raw = window.location.hash.replace(/^#/, "");
    if (!raw) {
      return null;
    }
    var params = new URLSearchParams(raw);
    var result = {};
    if (params.has("lat")) result.lat = parseFloat(params.get("lat"));
    if (params.has("lon")) result.lon = parseFloat(params.get("lon"));
    if (params.has("mode")) result.mode = params.get("mode");
    if (params.has("contours")) {
      result.contours = params
        .get("contours")
        .split(",")
        .map(function (item) {
          return parseInt(item, 10);
        })
        .filter(function (item) {
          return !isNaN(item);
        });
    }
    if (params.has("rings")) result.rings = params.get("rings") === "1";
    if (params.has("water")) result.excludeWater = params.get("water") === "1";
    if (params.has("generalize")) {
      var generalize = parseInt(params.get("generalize"), 10);
      result.generalize = isNaN(generalize) ? null : generalize;
    }
    if (params.has("zoom")) result.zoom = parseFloat(params.get("zoom"));
    if (params.has("center")) {
      var center = params.get("center").split(",").map(parseFloat);
      if (center.length === 2 && !center.some(isNaN)) {
        result.center = center;
      }
    }
    return result;
  }

  function writeHash() {
    var params = new URLSearchParams();
    params.set("lat", state.lat.toFixed(6));
    params.set("lon", state.lon.toFixed(6));
    params.set("mode", state.mode);
    params.set("contours", state.contours.join(","));
    params.set("rings", state.rings ? "1" : "0");
    params.set("water", state.excludeWater ? "1" : "0");
    if (state.generalize !== null) {
      params.set("generalize", String(state.generalize));
    }
    params.set("zoom", state.zoom.toFixed(2));
    params.set("center", state.center[0].toFixed(5) + "," + state.center[1].toFixed(5));
    var next = "#" + params.toString();
    if (next !== window.location.hash) {
      window.history.replaceState(null, "", next);
    }
  }

  function applyRestoredState() {
    var restored = parseHash();
    if (!restored) {
      return;
    }
    if (typeof restored.lat === "number" && !isNaN(restored.lat)) state.lat = restored.lat;
    if (typeof restored.lon === "number" && !isNaN(restored.lon)) state.lon = restored.lon;
    if (["pedestrian", "bicycle", "auto"].indexOf(restored.mode) >= 0) state.mode = restored.mode;
    if (restored.contours && restored.contours.length) {
      state.contours = restored.contours.slice(0, CONFIG.maxContours).sort(function (a, b) {
        return a - b;
      });
    }
    if (typeof restored.rings === "boolean") state.rings = restored.rings;
    if (typeof restored.excludeWater === "boolean") state.excludeWater = restored.excludeWater;
    if (restored.generalize !== undefined) state.generalize = restored.generalize;
    if (typeof restored.zoom === "number" && !isNaN(restored.zoom)) state.zoom = restored.zoom;
    if (restored.center) state.center = restored.center;
  }

  function renderCoordinates() {
    dom.lat.value = state.lat.toFixed(6);
    dom.lon.value = state.lon.toFixed(6);
  }

  function renderMode() {
    Array.prototype.forEach.call(dom.mode.children, function (button) {
      var active = button.dataset.mode === state.mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-checked", active ? "true" : "false");
    });
  }

  function renderContours() {
    dom.contours.innerHTML = "";
    CONTOUR_OPTIONS.filter(function (minutes) {
      return minutes >= CONFIG.minContourMinutes && minutes <= CONFIG.maxContourMinutes;
    }).forEach(function (minutes) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = String(minutes);
      chip.setAttribute("aria-pressed", "false");
      if (state.contours.indexOf(minutes) >= 0) {
        chip.classList.add("is-active");
        chip.setAttribute("aria-pressed", "true");
      } else if (state.contours.length >= CONFIG.maxContours) {
        chip.disabled = true;
      }
      chip.addEventListener("click", function () {
        toggleContour(minutes);
      });
      dom.contours.appendChild(chip);
    });
    dom.contoursCounter.textContent =
      "(" + state.contours.length + " из " + CONFIG.maxContours + ")";
    dom.build.disabled = pending || state.contours.length === 0;
  }

  function toggleContour(minutes) {
    var index = state.contours.indexOf(minutes);
    if (index >= 0) {
      state.contours.splice(index, 1);
    } else if (state.contours.length < CONFIG.maxContours) {
      state.contours.push(minutes);
    }
    state.contours.sort(function (a, b) {
      return a - b;
    });
    renderContours();
    writeHash();
  }

  function renderGeneralize() {
    var auto = state.generalize === null;
    dom.generalizeAuto.checked = auto;
    dom.generalize.disabled = auto;
    dom.generalize.value = auto ? 100 : state.generalize;
    dom.generalizeValue.textContent = auto ? "по умолчанию" : state.generalize + " м";
  }

  function renderToggles() {
    dom.rings.checked = state.rings;
    dom.excludeWater.checked = state.excludeWater;
  }

  function showError(title, detail, meta) {
    dom.errorTitle.textContent = title;
    dom.errorDetail.textContent = detail || "";
    dom.errorMeta.textContent = meta || "";
    dom.error.hidden = false;
  }

  function hideError() {
    dom.error.hidden = true;
  }

  function formatNumber(value, digits) {
    if (value === null || value === undefined) {
      return "—";
    }
    return Number(value).toLocaleString("ru-RU", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  function renderResults(payload, elapsedMs) {
    var metadata = payload.metadata || {};

    dom.legend.innerHTML = "";
    payload.features.forEach(function (feature) {
      var properties = feature.properties || {};
      var item = document.createElement("li");
      item.className = "legend__item";

      var swatch = document.createElement("span");
      swatch.className = "legend__swatch";
      swatch.style.background = properties.color || "#2b83ba";

      var label = document.createElement("span");
      label.className = "legend__label";
      label.textContent = (metadata.rings ? "" : "≤ ") + properties.contour_minutes + " мин";

      var value = document.createElement("span");
      value.className = "legend__value";
      value.textContent = formatNumber(properties.area_km2, 2) + " км²";

      item.appendChild(swatch);
      item.appendChild(label);
      item.appendChild(value);
      dom.legend.appendChild(item);
    });

    var rows = [
      ["Время API", formatNumber(metadata.duration_ms, 0) + " мс"],
      ["Время движка", formatNumber(metadata.engine_ms, 0) + " мс"],
      ["Время в браузере", formatNumber(elapsedMs, 0) + " мс"],
      ["Кэш", metadata.cache === "hit" ? "hit" : "miss"],
      [
        "Снап к графу",
        metadata.snap_distance_m === null || metadata.snap_distance_m === undefined
          ? "—"
          : formatNumber(metadata.snap_distance_m, 1) + " м"
      ],
      ["Данные OSM", metadata.osm_data_timestamp || "—"],
      ["request_id", metadata.request_id || "—"]
    ];

    dom.stats.innerHTML = "";
    rows.forEach(function (row) {
      var dt = document.createElement("dt");
      dt.textContent = row[0];
      var dd = document.createElement("dd");
      dd.textContent = row[1];
      dom.stats.appendChild(dt);
      dom.stats.appendChild(dd);
    });

    dom.results.hidden = false;
  }

  function boundsOf(payload) {
    var bounds = null;
    payload.features.forEach(function (feature) {
      var geometry = feature.geometry;
      if (!geometry) {
        return;
      }
      var polygons =
        geometry.type === "MultiPolygon" ? geometry.coordinates : [geometry.coordinates];
      polygons.forEach(function (polygon) {
        polygon.forEach(function (ring) {
          ring.forEach(function (position) {
            if (bounds === null) {
              bounds = new maplibregl.LngLatBounds(position, position);
            } else {
              bounds.extend(position);
            }
          });
        });
      });
    });
    return bounds;
  }

  var map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        basemap: {
          type: "raster",
          tiles: [CONFIG.basemapTileUrl],
          tileSize: 256,
          maxzoom: 19,
          attribution: CONFIG.basemapAttribution
        }
      },
      layers: [{ id: "basemap", type: "raster", source: "basemap" }]
    },
    center: state.center,
    zoom: state.zoom
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-left");

  var markerElement = document.createElement("div");
  markerElement.className = "marker";

  var marker = new maplibregl.Marker({ element: markerElement, draggable: true })
    .setLngLat([state.lon, state.lat])
    .addTo(map);

  marker.on("dragend", function () {
    var position = marker.getLngLat();
    state.lat = position.lat;
    state.lon = position.lng;
    renderCoordinates();
    writeHash();
  });

  map.on("load", function () {
    map.addSource(SOURCE_ID, {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] }
    });

    map.addLayer({
      id: "isochrones-fill",
      type: "fill",
      source: SOURCE_ID,
      paint: {
        "fill-color": ["get", "color"],
        "fill-opacity": ["coalesce", ["get", "fill_opacity"], 0.25]
      }
    });

    map.addLayer({
      id: "isochrones-outline",
      type: "line",
      source: SOURCE_ID,
      paint: {
        "line-color": ["get", "color"],
        "line-width": 1.6,
        "line-opacity": 0.9
      }
    });

    if (lastResponse) {
      map.getSource(SOURCE_ID).setData(lastResponse);
    }
  });

  map.on("click", function (event) {
    state.lat = event.lngLat.lat;
    state.lon = event.lngLat.lng;
    marker.setLngLat([state.lon, state.lat]);
    renderCoordinates();
    writeHash();
    dom.mapHint.hidden = true;
  });

  map.on("moveend", function () {
    var center = map.getCenter();
    state.center = [center.lng, center.lat];
    state.zoom = map.getZoom();
    writeHash();
  });

  function setPending(value) {
    pending = value;
    dom.build.disabled = value || state.contours.length === 0;
    dom.build.textContent = value ? "Считаем…" : "Построить";
  }

  function buildRequestBody() {
    var options = {
      rings: state.rings,
      exclude_water: state.excludeWater
    };
    if (state.generalize !== null) {
      options.generalize = state.generalize;
    }
    return {
      lat: state.lat,
      lon: state.lon,
      contours: state.contours.slice(),
      mode: state.mode,
      options: options
    };
  }

  async function requestIsochrone() {
    if (pending) {
      return;
    }
    if (!state.contours.length) {
      showError("Не выбраны контуры", "Выберите хотя бы одно значение времени.", "");
      return;
    }

    hideError();
    setPending(true);
    var startedAt = performance.now();

    try {
      var response = await fetch(CONFIG.apiBase + "/isochrone", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildRequestBody())
      });

      var elapsedMs = performance.now() - startedAt;
      var payload = null;

      try {
        payload = await response.json();
      } catch (parseError) {
        payload = null;
      }

      if (!response.ok) {
        var title = (payload && payload.title) || "Ошибка " + response.status;
        var detail = (payload && payload.detail) || "Сервис вернул ответ без описания проблемы.";
        var meta = payload
          ? [payload.code, payload.request_id].filter(Boolean).join(" · ")
          : "HTTP " + response.status;
        showError(title, detail, meta);
        dom.results.hidden = true;
        return;
      }

      lastResponse = payload;

      var source = map.getSource(SOURCE_ID);
      if (source) {
        source.setData(payload);
      }

      var bounds = boundsOf(payload);
      if (bounds) {
        map.fitBounds(bounds, { padding: 40, duration: 600, maxZoom: 15 });
      }

      renderResults(payload, elapsedMs);
      writeHash();
    } catch (networkError) {
      showError(
        "Сеть недоступна",
        "Не удалось обратиться к API: " + networkError.message,
        ""
      );
    } finally {
      setPending(false);
    }
  }

  function downloadGeoJSON() {
    if (!lastResponse) {
      return;
    }
    var blob = new Blob([JSON.stringify(lastResponse, null, 2)], {
      type: "application/geo+json"
    });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    var stamp = new Date().toISOString().replace(/[:.]/g, "-");
    link.href = url;
    link.download =
      "isochrone_" + state.mode + "_" + state.contours.join("-") + "_" + stamp + ".geojson";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  dom.lat.addEventListener("change", function () {
    var value = parseFloat(dom.lat.value);
    if (!isNaN(value)) {
      state.lat = clamp(value, -90, 90);
      marker.setLngLat([state.lon, state.lat]);
      renderCoordinates();
      writeHash();
    }
  });

  dom.lon.addEventListener("change", function () {
    var value = parseFloat(dom.lon.value);
    if (!isNaN(value)) {
      state.lon = clamp(value, -180, 180);
      marker.setLngLat([state.lon, state.lat]);
      renderCoordinates();
      writeHash();
    }
  });

  dom.mode.addEventListener("click", function (event) {
    var button = event.target.closest("[data-mode]");
    if (!button) {
      return;
    }
    state.mode = button.dataset.mode;
    renderMode();
    writeHash();
  });

  dom.rings.addEventListener("change", function () {
    state.rings = dom.rings.checked;
    writeHash();
    if (lastResponse) {
      requestIsochrone();
    }
  });

  dom.excludeWater.addEventListener("change", function () {
    state.excludeWater = dom.excludeWater.checked;
    writeHash();
  });

  dom.generalizeAuto.addEventListener("change", function () {
    state.generalize = dom.generalizeAuto.checked ? null : parseInt(dom.generalize.value, 10);
    renderGeneralize();
    writeHash();
  });

  dom.generalize.addEventListener("input", function () {
    state.generalize = parseInt(dom.generalize.value, 10);
    renderGeneralize();
  });

  dom.generalize.addEventListener("change", writeHash);
  dom.build.addEventListener("click", requestIsochrone);
  dom.download.addEventListener("click", downloadGeoJSON);

  applyRestoredState();
  renderCoordinates();
  renderMode();
  renderContours();
  renderToggles();
  renderGeneralize();
  marker.setLngLat([state.lon, state.lat]);
  map.jumpTo({ center: state.center, zoom: state.zoom });
  writeHash();
})();
