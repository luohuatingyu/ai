# 可视化模型训练平台后端
# 基础镜像: Python 3.12 (Debian slim)
# GPU 运行时: 宿主机 NVIDIA 驱动 + nvidia-container-toolkit 挂载进容器，
#             torch 的 Linux wheel 内置 CUDA 12 运行时，镜像内无需安装 CUDA Toolkit。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180

WORKDIR /app

COPY requirements.txt ./
# BuildKit 缓存挂载：网络中断重试时保留已下载的 wheel，不必从头装
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 10 -r requirements.txt

COPY app ./app
COPY static ./static
COPY sample_data ./sample_data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
