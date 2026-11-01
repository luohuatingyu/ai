# 多阶段构建：Node 构建 + Nginx 托管
FROM node:20-alpine AS builder

WORKDIR /app

# 安装依赖（利用 Docker 层缓存）
COPY package.json package-lock.json* ./
RUN npm install --registry=https://registry.npmmirror.com

# 复制源码并构建
COPY . .
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# 生产镜像：Nginx 托管静态资源并反向代理 API
FROM nginx:1.25-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]