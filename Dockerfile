# Self-contained MCP runtime. No Dash/Zeal application, Node.js, or local pip package.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOCSET_HOME=/data

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 docsets \
    && useradd --uid 10001 --gid docsets --no-create-home --home-dir /data docsets \
    && mkdir -p /data \
    && chown 10001:10001 /data

COPY source/ ./source/
COPY profiles/ ./profiles/

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8765

ENTRYPOINT ["python", "/app/source/app.py"]
CMD ["serve-http", "--host", "0.0.0.0", "--port", "8765"]
