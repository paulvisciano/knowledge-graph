FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY mcp-services/ /app/mcp-services/

RUN pip install --no-cache-dir \
    -e /app/mcp-services/ \
    starlette

EXPOSE 9653

CMD ["knowledge-graph-mcp"]