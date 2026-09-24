FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir -e .
ENV DATA_DIR=/app/data APP_HOST=0.0.0.0 APP_PORT=8787
EXPOSE 8787
CMD ["wb-control", "run"]
