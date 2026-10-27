# 前端容器：Node 零依赖静态服务 + /api 反向代理
# 构建期把 backend/static 现有页面拷入镜像；国内网络可通过
# .env 的 NODE_BASE_IMAGE 换国内 Node 镜像源。
ARG NODE_BASE=node:20-alpine
FROM ${NODE_BASE}

WORKDIR /app
COPY frontend/server.js /app/server.js
COPY backend/static/ /srv/

ENV FRONTEND_ROOT=/srv FRONTEND_PORT=8000 API_PROXY_HOST=trainer API_PROXY_PORT=8000

EXPOSE 8000
CMD ["node", "/app/server.js"]
