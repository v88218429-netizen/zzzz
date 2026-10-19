FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md VERSION ./
COPY plugin.json mcp.json START_WB_AI_MANAGER.command ./
RUN mkdir -p ./scripts
COPY scripts/plugin_mcp_stdio.sh scripts/update_smoke.py ./scripts/
COPY src ./src
COPY config ./config
COPY data ./data
RUN pip install --no-cache-dir -e .
ENV DATA_DIR=/app/data APP_HOST=0.0.0.0 APP_PORT=8787
EXPOSE 8787
CMD ["wb-control", "run"]
