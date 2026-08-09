import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";
const API = `${BASE_URL}/api/v1`;
const TARGET_RPS = Number(__ENV.TARGET_RPS || 10);
const DURATION = __ENV.DURATION || "5m";

const engineDuration = new Trend("engine_duration_ms");
const apiDuration = new Trend("api_duration_ms");
const cacheHitRate = new Rate("cache_hit_rate");
const serverErrors = new Counter("server_errors");

export const options = {
  scenarios: {
    mixed: {
      executor: "constant-arrival-rate",
      rate: TARGET_RPS,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 60
    }
  },
  thresholds: {
    http_req_duration: ["p(95)<3000"],
    http_req_failed: ["rate<0.005"],
    server_errors: ["count<1"]
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"]
};

const ORIGINS = [
  { lat: 43.238949, lon: 76.889709 },
  { lat: 43.244, lon: 76.868 },
  { lat: 43.2202, lon: 76.8512 },
  { lat: 43.2585, lon: 76.9435 },
  { lat: 43.3521, lon: 77.0405 },
  { lat: 43.2361, lon: 76.9302 }
];

const MODES = ["pedestrian", "pedestrian", "bicycle", "auto"];
const CONTOUR_SETS = [[10, 20, 30], [15], [10, 20], [60], [5, 10, 15, 20]];

function pick(items) {
  return items[Math.floor(Math.random() * items.length)];
}

function jitter(value) {
  return Number((value + (Math.random() - 0.5) * 0.02).toFixed(6));
}

export default function () {
  const origin = pick(ORIGINS);
  const body = JSON.stringify({
    lat: jitter(origin.lat),
    lon: jitter(origin.lon),
    contours: pick(CONTOUR_SETS),
    mode: pick(MODES),
    options: { rings: Math.random() < 0.25, exclude_water: true }
  });

  const response = http.post(`${API}/isochrone`, body, {
    headers: { "Content-Type": "application/json" },
    tags: { name: "POST /api/v1/isochrone" }
  });

  const ok = check(response, {
    "status is 200": (r) => r.status === 200,
    "body is a FeatureCollection": (r) =>
      r.status === 200 && r.json("type") === "FeatureCollection",
    "no server error": (r) => r.status < 500
  });

  if (response.status >= 500) {
    serverErrors.add(1);
  }

  if (response.status === 200) {
    apiDuration.add(response.json("metadata.duration_ms"));
    engineDuration.add(response.json("metadata.engine_ms"));
    cacheHitRate.add(response.headers["X-Cache"] === "HIT");
  }

  if (!ok && response.status >= 400 && response.status < 500) {
    console.warn(`unexpected client error ${response.status}: ${response.body}`);
  }

  sleep(0.1);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(
      {
        requests: data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0,
        failed_rate: data.metrics.http_req_failed
          ? data.metrics.http_req_failed.values.rate
          : null,
        p50_ms: data.metrics.http_req_duration.values.med,
        p95_ms: data.metrics.http_req_duration.values["p(95)"],
        p99_ms: data.metrics.http_req_duration.values["p(99)"],
        cache_hit_rate: data.metrics.cache_hit_rate
          ? data.metrics.cache_hit_rate.values.rate
          : null
      },
      null,
      2
    )
  };
}
