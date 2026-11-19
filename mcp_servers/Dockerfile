# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.

FROM python:3.11-slim

WORKDIR /app

COPY mcp_servers/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./pyproject.toml
COPY sdk/ ./sdk/
RUN pip install --no-cache-dir ".[mcp]"

COPY mcp_servers/ ./mcp_servers/

RUN groupadd -r mcpuser && useradd -r -g mcpuser -d /app -s /sbin/nologin mcpuser \
    && chown -R mcpuser:mcpuser /app

USER mcpuser

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV MCP_HOST=0.0.0.0
EXPOSE 8080

# Run the FastMCP server with SSE transport for Cloud Run deployment
ENTRYPOINT ["python", "-m", "mcp_servers.src.server", "--transport", "sse"]
