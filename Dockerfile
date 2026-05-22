# syntax=docker/dockerfile:1

# ---------- 依赖安装（uv） ----------
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 先复制锁文件，利用 Docker 层缓存
COPY uv.lock pyproject.toml ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ---------- 运行镜像 ----------
FROM python:3.11-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Node.js（PyExecJS / 部分平台脚本）、ffmpeg、时区
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs ffmpeg tzdata && \
    ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app /app

CMD ["python", "main.py"]
