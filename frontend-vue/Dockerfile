# 多阶段构建：Node 构建 Vue 应用 → 零依赖 Node 服务托管（与旧版 frontend 同模式，无 nginx）
FROM node:20-alpine AS builder

WORKDIR /app

# 安装依赖（利用 Docker 层缓存；lockfile 保证可复现）
COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com --no-audit --no-fund

# 复制源码并构建（vue-tsc 类型检查 + vite build）
COPY . .
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# 生产镜像：Node 静态服务 + /api 反向代理
FROM node:20-alpine

WORKDIR /srv
COPY --from=builder /app/dist ./dist
COPY server.js ./server.js

ENV FRONTEND_ROOT=/srv/dist \
    FRONTEND_PORT=8000 \
    API_PROXY_HOST=trainer \
    API_PROXY_PORT=8000

EXPOSE 8000

CMD ["node", "server.js"]