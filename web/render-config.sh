#!/bin/sh
set -eu

TARGET=/usr/share/nginx/html/config.js

cat > "$TARGET" <<EOF
window.APP_CONFIG = {
  apiBase: "/api/v1",
  basemapTileUrl: "${BASEMAP_TILE_URL:-https://tile.openstreetmap.org/{z}/{x}/{y}.png}",
  basemapAttribution: "${BASEMAP_ATTRIBUTION:-&copy; OpenStreetMap contributors}",
  maxContours: ${MAX_CONTOURS:-4},
  minContourMinutes: ${MIN_CONTOUR_MINUTES:-5},
  maxContourMinutes: ${MAX_CONTOUR_MINUTES:-60}
};
EOF

echo "render-config: wrote $TARGET"
