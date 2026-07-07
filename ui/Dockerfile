FROM oven/bun:1 AS builder

WORKDIR /app

COPY ui/package.json ui/bun.lock* /app/
RUN bun install

COPY ui/ /app/
RUN VITE_BUILDING=1 bun x vite build

FROM nginx:alpine

COPY --from=builder /app/build /usr/share/nginx/html
COPY docker/nginx/nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:3000/ || exit 1