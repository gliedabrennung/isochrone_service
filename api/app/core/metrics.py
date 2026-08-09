from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)

REQUEST_DURATION = Histogram(
    "isochrone_request_duration_seconds",
    "End-to-end duration of isochrone requests",
    labelnames=("mode", "max_contour_bucket", "cache"),
    buckets=(0.02, 0.05, 0.1, 0.25, 0.5, 0.8, 1.2, 2.0, 3.0, 5.0, 10.0),
    registry=REGISTRY,
)

REQUESTS_TOTAL = Counter(
    "isochrone_requests_total",
    "Total number of isochrone requests by mode and HTTP status",
    labelnames=("mode", "status"),
    registry=REGISTRY,
)

ENGINE_DURATION = Histogram(
    "isochrone_engine_duration_seconds",
    "Duration of the Valhalla /isochrone call",
    labelnames=("mode",),
    buckets=(0.02, 0.05, 0.1, 0.25, 0.5, 0.8, 1.2, 2.0, 3.0, 5.0, 10.0),
    registry=REGISTRY,
)

CACHE_HITS = Counter(
    "isochrone_cache_hits_total",
    "Number of isochrone responses served from cache",
    registry=REGISTRY,
)

CACHE_MISSES = Counter(
    "isochrone_cache_misses_total",
    "Number of isochrone responses computed by the engine",
    registry=REGISTRY,
)

CACHE_ERRORS = Counter(
    "isochrone_cache_errors_total",
    "Number of cache operations that failed and were degraded to a no-cache path",
    labelnames=("operation",),
    registry=REGISTRY,
)

RATE_LIMITED = Counter(
    "isochrone_rate_limited_total",
    "Number of requests rejected by the per-IP rate limiter",
    registry=REGISTRY,
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
