FROM python:3.12-bookworm AS locale-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends locales \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/locale \
    && localedef --no-archive \
        --inputfile=zh_CN \
        --charmap=GB18030 \
        /opt/locale/zh_CN.GB18030

# 使用官方国内可顺畅拉取的 python 基础镜像
FROM python:3.12-slim-bookworm

# 从 ghcr.io 官方复制 uv 二进制文件（或者通过脚本离线/国内源安装）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 如果上面 COPY ghcr.io 依然慢，可以直接用 curl 从国内镜像/脚本安装 uv
# RUN curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY --from=locale-builder /opt/locale/zh_CN.GB18030 /usr/lib/locale/zh_CN.GB18030

ENV LANG=C \
    LC_ALL=C

COPY src ./src
COPY scripts/test_login.py scripts/_login_check.py ./scripts/
RUN mkdir -p flow/md flow/td logs

CMD ["uv", "run", "--no-sync", "python", "src/main.py"]
