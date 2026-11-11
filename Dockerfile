# For machines without a TuringDB wheel (macOS 14 or older, Intel Macs, Windows).
#   docker compose up -d        -> TuringDB on localhost:6666, visualizer on localhost:8080
FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev
COPY . .
ENV LISTEN=0.0.0.0
EXPOSE 6666 8080
CMD ["bash", "-c", "./start.sh && tail -F .turing/logs/* 2>/dev/null || sleep infinity"]
