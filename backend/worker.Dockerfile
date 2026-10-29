# 远程计算节点专用镜像：只安装训练依赖，不包含 API、数据库管理和测试工具。
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG WORKER_MODE=gpu
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    USE_DATABASE=false
LABEL org.mtp.worker.mode=${WORKER_MODE}
WORKDIR /app

# 上传构建包时 requirements-worker.txt 映射为 requirements.txt。
COPY requirements.txt ./
# CPU 只从 PyTorch 官方 CPU 索引安装 torch 本体，其他依赖仍使用配置的 pip 源。
# 任何下载/安装失败都显式失败，不回退到带 CUDA 的 wheel；缓存仅存在于构建器。
RUN --mount=type=cache,target=/root/.cache/pip \
    case "$WORKER_MODE" in cpu|gpu) ;; *) echo 'WORKER_MODE must be cpu or gpu' >&2; exit 2;; esac; \
    for attempt in 1 2 3 4 5; do \
      if { [ "$WORKER_MODE" != cpu ] || \
           pip install --retries 10 --no-deps --index-url https://download.pytorch.org/whl/cpu \
             "$(sed -n '/^torch[<=>]/p' requirements.txt)"; } && \
         pip install --retries 10 -r requirements.txt && \
         pip check; then \
        if [ "$WORKER_MODE" = cpu ]; then \
          python -c 'import torch; assert torch.version.cuda is None, "CPU worker must not include CUDA"' || exit 1; \
        fi; \
        exit 0; \
      fi; \
      echo "worker dependencies failed (attempt ${attempt}/5)" >&2; sleep 3; \
    done; exit 1

COPY app ./app
CMD ["python", "-m", "app.worker"]
