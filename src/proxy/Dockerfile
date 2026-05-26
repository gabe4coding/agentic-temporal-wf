FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN pip install --no-cache-dir uv && uv sync --no-dev --frozen || uv sync --no-dev
COPY src/ ./src/
ENV CREDENTIAL_PROXY_BOOT=1
CMD ["uv", "run", "uvicorn", "src.proxy.credential_proxy:app", "--host", "0.0.0.0", "--port", "8443"]
