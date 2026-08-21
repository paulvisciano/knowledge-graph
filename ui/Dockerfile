FROM oven/bun:1 AS builder

WORKDIR /app

COPY ui/package.json ui/bun.lock* /app/
RUN bun install

COPY ui/ /app/
RUN VITE_BUILDING=1 bun x vite build

FROM nginx:alpine

COPY --from=builder /app/build /usr/share/nginx/html
COPY docker/nginx/nginx.conf /etc/nginx/templates/default.conf.template
COPY docker/nginx/certs/kg.crt /etc/nginx/certs/kg.crt
COPY docker/nginx/certs/kg.key /etc/nginx/certs/kg.key

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf https://localhost:3443/ --insecure || exit 1