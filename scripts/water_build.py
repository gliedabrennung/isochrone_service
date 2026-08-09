import argparse
import json
import re
import sys
from collections import defaultdict

from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union
from shapely.validation import make_valid

AREAL_TYPES = ("Polygon", "MultiPolygon")
LINEAR_TYPES = ("LineString", "MultiLineString")
WIDTH_PATTERN = re.compile(r'"width"\s*=>\s*"([^"]+)"')
NUMBER_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)")
MIN_BUFFER_M = 1.0


def log(message):
    print(f"water_build {message}", file=sys.stderr, flush=True)


def load_features(path):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return []
    return payload.get("features") or []


def parse_bbox(raw):
    parts = [float(value) for value in raw.split(",")] if raw else []
    if len(parts) != 4:
        return None
    return parts


def bbox_center(bbox, features):
    if bbox:
        return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
    for feature in features:
        geometry = feature.get("geometry")
        if geometry:
            centroid = shape(geometry).centroid
            return centroid.x, centroid.y
    return 0.0, 0.0


def repair(geometry):
    if geometry.is_empty:
        return geometry
    if geometry.is_valid:
        return geometry
    fixed = geometry.buffer(0)
    if fixed.is_valid and not fixed.is_empty:
        return fixed
    return make_valid(geometry)


def polygonal_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    if geometry.geom_type == "GeometryCollection":
        parts = []
        for part in geometry.geoms:
            parts.extend(polygonal_parts(part))
        return parts
    return []


def tag_width(properties):
    raw = properties.get("width")
    if not raw:
        other = properties.get("other_tags") or ""
        match = WIDTH_PATTERN.search(other)
        raw = match.group(1) if match else None
    if not raw:
        return None
    match = NUMBER_PATTERN.match(str(raw))
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0 else None


def build_areas(features, transformer, min_area_m2):
    kept = []
    for feature in features:
        geometry = feature.get("geometry")
        if not geometry or geometry.get("type") not in AREAL_TYPES:
            continue
        projected = repair(transform(transformer, shape(geometry)))
        for part in polygonal_parts(projected):
            if part.area >= min_area_m2:
                kept.append(part)
    return kept


def build_lines(features, transformer, widths):
    grouped = defaultdict(list)
    for feature in features:
        geometry = feature.get("geometry")
        if not geometry or geometry.get("type") not in LINEAR_TYPES:
            continue
        properties = feature.get("properties") or {}
        waterway = properties.get("waterway")
        if waterway not in widths:
            continue
        width = tag_width(properties) or widths[waterway]
        buffer_distance = max(float(width) / 2.0, MIN_BUFFER_M)
        grouped[round(buffer_distance, 1)].append(transform(transformer, shape(geometry)))

    polygons = []
    for buffer_distance, geometries in grouped.items():
        merged = unary_union(geometries)
        buffered = repair(merged.buffer(buffer_distance, resolution=4, cap_style=2))
        polygons.extend(polygonal_parts(buffered))
    return polygons


def main():
    parser = argparse.ArgumentParser(
        description="Build the unified water layer for isochrone clipping"
    )
    parser.add_argument("--areas", required=True)
    parser.add_argument("--lines", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--bbox", default="")
    parser.add_argument("--min-area-m2", type=float, default=5000.0)
    parser.add_argument("--river-width-m", type=float, default=15.0)
    parser.add_argument("--stream-width-m", type=float, default=4.0)
    parser.add_argument("--canal-width-m", type=float, default=10.0)
    args = parser.parse_args()

    area_features = load_features(args.areas)
    line_features = load_features(args.lines)
    log(f"input: {len(area_features)} areal, {len(line_features)} linear features")

    bbox = parse_bbox(args.bbox)
    lon0, lat0 = bbox_center(bbox, area_features or line_features)
    metric_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
    to_metric = Transformer.from_crs(CRS.from_epsg(4326), metric_crs, always_xy=True).transform
    to_wgs84 = Transformer.from_crs(metric_crs, CRS.from_epsg(4326), always_xy=True).transform

    widths = {
        "river": args.river_width_m,
        "stream": args.stream_width_m,
        "canal": args.canal_width_m,
    }

    polygons = build_areas(area_features, to_metric, args.min_area_m2)
    log(f"areal polygons kept after the {args.min_area_m2:.0f} m2 filter: {len(polygons)}")

    line_polygons = build_lines(line_features, to_metric, widths)
    log(f"buffered linear waterways: {len(line_polygons)}")

    polygons.extend(line_polygons)

    if polygons:
        merged = repair(unary_union(polygons))
        parts = polygonal_parts(merged)
    else:
        parts = []

    features = [
        {
            "type": "Feature",
            "geometry": mapping(transform(to_wgs84, part)),
            "properties": {"area_m2": round(part.area, 1)},
        }
        for part in parts
    ]

    payload = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "min_area_m2": args.min_area_m2,
            "river_width_m": args.river_width_m,
            "stream_width_m": args.stream_width_m,
            "canal_width_m": args.canal_width_m,
            "projection": metric_crs.to_proj4(),
        },
    }

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    log(f"written {len(features)} merged water parts to {args.out}")


if __name__ == "__main__":
    main()
