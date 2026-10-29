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
RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s#http://deb.debian.org#http://${APT_MIRROR}#g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# BuildKit 缓存挂载：网络中断重试时保留已下载的 wheel，不必从头装
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 10 -r requirements.txt

COPY app ./app
COPY static ./static
COPY sample_data ./sample_data
COPY Dockerfile ./Dockerfile

# 启动与数据库初始化脚本
COPY init_db.sh start.sh ./
RUN chmod +x init_db.sh start.sh

EXPOSE 8000

# 启动：等待/初始化 MySQL（失败不阻塞训练服务）后启动 API
CMD ["./start.sh"]
