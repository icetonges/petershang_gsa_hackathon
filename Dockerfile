# syntax=docker/dockerfile:1
# Container image for remote (streamable-HTTP) deployment.
#
# Databricks Apps does not use this file: it builds from the synced source
# and deploy/databricks/app.yaml. Keep it for any other container host and
# for running the server in isolation locally.
#
# The server serves MCP over streamable HTTP at :8080/mcp, health at
# :8080/health. app.py selects HTTP because PORT is set below.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV PORT=8080

WORKDIR /app

# Install uv (fast, reproducible installs).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Dependencies first, for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

EXPOSE 8080

# PORT=8080 (set above) makes app.py serve HTTP on that port.
CMD ["python", "-m", "mcp_server_creator.app"]
