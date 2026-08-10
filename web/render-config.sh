#!/bin/sh
set -eu

TARGET=/usr/share/nginx/html/config.js

DEFAULT_TILE_URL='https://tile.openstreetmap.org/{z}/{x}/{y}.png'
DEFAULT_ATTRIBUTION='&copy; OpenStreetMap contributors'

TILE_URL="${BASEMAP_TILE_URL-}"
[ -n "$TILE_URL" ] || TILE_URL="$DEFAULT_TILE_URL"

ATTRIBUTION="${BASEMAP_ATTRIBUTION-}"
[ -n "$ATTRIBUTION" ] || ATTRIBUTION="$DEFAULT_ATTRIBUTION"

cat > "$TARGET" <<EOF
window.APP_CONFIG = {
  apiBase: "/api/v1",
  basemapTileUrl: "$TILE_URL",
  basemapAttribution: "$ATTRIBUTION",
  maxContours: ${MAX_CONTOURS:-4},
  minContourMinutes: ${MIN_CONTOUR_MINUTES:-5},
  maxContourMinutes: ${MAX_CONTOUR_MINUTES:-60}
};
EOF

echo "render-config: wrote $TARGET (basemap $TILE_URL)"
