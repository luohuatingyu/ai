# 可视化模型训练平台后端
# 基础镜像: Python 3.12 (Debian slim)
# GPU 运行时: 宿主机 NVIDIA 驱动 + nvidia-container-toolkit 挂载进容器，
#             torch 的 Linux wheel 内置 CUDA 12 运行时，镜像内无需安装 CUDA Toolkit。
# 基础镜像与远程仓库源均可通过 build-arg 覆盖（远程目标机构建时注入国内加速源）：
#   BASE_IMAGE    Docker Hub 基础镜像（镜像加速：<registry>/library/python:3.12-slim）
#   PIP_INDEX_URL pip 源（默认清华）
#   APT_MIRROR    Debian apt 源 host（默认留空走官方 deb.debian.org）
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG APT_MIRROR=""
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

# API 通过 OpenSSH 客户端远程执行已打包的 worker 引导脚本。
# 外壳重试循环：网络不稳定时 apt-get update 偶发 502/连接失败，兜底重试。
RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s#http://deb.debian.org#http://${APT_MIRROR}#g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    attempt=0; \
    until apt-get update; do \
      attempt=$((attempt + 1)); \
      [ "$attempt" -ge 5 ] && exit 1; \
      echo "apt-get update 失败（第 ${attempt}/5 次），3 秒后重试..."; sleep 3; \
    done; \
    apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# BuildKit 缓存挂载：网络中断重试时保留已下载的 wheel，不必从头装。
# 外壳重试循环：pip 对 hash 校验失败（下载被截断）不自动重试，这里兜底重跑整次安装，
# 已成功下载的 wheel 会命中缓存，不会重复拉取。
RUN --mount=type=cache,target=/root/.cache/pip \
    for attempt in 1 2 3 4 5; do \
      if pip install --retries 10 -r requirements.txt; then exit 0; fi; \
      echo "pip install 失败（第 ${attempt}/5 次），3 秒后重试..."; sleep 3; \
    done; \
    exit 1

COPY app ./app
COPY static ./static
COPY sample_data ./sample_data
COPY Dockerfile ./Dockerfile
# 平台负责打包远程 worker，保留独立的精简构建配方和依赖清单。
COPY worker.Dockerfile requirements-worker.txt ./

# 启动与数据库初始化脚本
COPY init_db.sh start.sh ./
RUN chmod +x init_db.sh start.sh

EXPOSE 8000

# 启动：等待/初始化 MySQL（失败不阻塞训练服务）后启动 API
CMD ["./start.sh"]
