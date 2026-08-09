FROM alpine:3.23.5 AS vendor

ARG MAPLIBRE_VERSION=4.7.1

RUN apk add --no-cache curl \
    && mkdir -p /vendor \
    && curl -fsSL -o /vendor/maplibre-gl.js \
        "https://unpkg.com/maplibre-gl@${MAPLIBRE_VERSION}/dist/maplibre-gl.js" \
    && curl -fsSL -o /vendor/maplibre-gl.css \
        "https://unpkg.com/maplibre-gl@${MAPLIBRE_VERSION}/dist/maplibre-gl.css"

FROM nginx:1.29.8-alpine3.23

COPY --from=vendor /vendor/ /usr/share/nginx/html/vendor/
COPY index.html app.js style.css /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY render-config.sh /docker-entrypoint.d/40-render-config.sh

RUN chmod +x /docker-entrypoint.d/40-render-config.sh

EXPOSE 80
