FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 复制源码和模板
COPY src/ ./src/
COPY web/ ./web/
RUN mkdir -p /app/data

ENV RUNDOWN_DATA_DIR=/app/data
ENV RUNDOWN_SERVE_MODE=true
ENV RUNDOWN_NON_INTERACTIVE=true
ENV MCP_TRANSPORT=sse

EXPOSE 8080

CMD ["python", "-c", "from src.main import cmd_serve; cmd_serve()"]
