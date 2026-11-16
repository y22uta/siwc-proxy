FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY muse_bridge ./muse_bridge
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 bridge && mkdir -p /data && chown bridge:bridge /data
USER bridge
EXPOSE 8088
CMD ["uvicorn", "muse_bridge.main:app", "--host", "0.0.0.0", "--port", "8088", "--workers", "1"]
