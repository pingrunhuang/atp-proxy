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

# 国内机拉 ghcr 慢：用官方 install 脚本装 uv（与 libgomp1 合并一层，少一次 apt）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates libgomp1 \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -sf /root/.local/bin/uv /usr/local/bin/uv \
    && ln -sf /root/.local/bin/uvx /usr/local/bin/uvx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY --from=locale-builder /opt/locale/zh_CN.GB18030 /usr/lib/locale/zh_CN.GB18030

ENV LANG=C \
    LC_ALL=C

COPY src ./src
COPY scripts/test_login.py scripts/_login_check.py ./scripts/
RUN mkdir -p flow/md flow/td logs

CMD ["uv", "run", "--no-sync", "python", "src/main.py"]
