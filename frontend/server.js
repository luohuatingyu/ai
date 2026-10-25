/* 前端容器服务：静态托管现有单页 + /api 反向代理到后端（零依赖，仅 Node 内置模块）。
 *
 * 设计要点：
 * - 静态文件来自构建期 COPY 的 backend/static（原生 HTML/JS/CSS，无构建链）；
 * - /api/* 原样转发到后端 trainer 容器（流式管道，支持下载/上传/SSE 场景）；
 * - /healthz 供容器健康检查；
 * - 页面与 API 同源（同一端口），无需 CORS 配置。
 */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = process.env.FRONTEND_ROOT || '/srv';          // 静态文件根目录
const PORT = Number(process.env.FRONTEND_PORT || 8000);    // 监听端口
const API_HOST = process.env.API_PROXY_HOST || 'trainer';  // 后端容器名
const API_PORT = Number(process.env.API_PROXY_PORT || 8000);

// 常用静态类型映射（未命中回退 application/octet-stream）
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
};

/** 统一响应出口：写状态码/头/正文。 */
function send(res, status, body, headers) {
  res.writeHead(status, Object.assign(
    { 'Content-Type': 'text/plain; charset=utf-8' }, headers || {}));
  res.end(body);
}

/** 静态文件服务：根路径映射 index.html，防目录穿越。 */
function serveStatic(req, res) {
  let urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
  if (urlPath === '/') urlPath = '/index.html';
  const root = path.normalize(ROOT);
  const filePath = path.normalize(path.join(root, urlPath));
  if (filePath !== path.join(root, 'index.html') && !filePath.startsWith(root + path.sep)) {
    return send(res, 403, 'Forbidden');
  }
  fs.readFile(filePath, (err, data) => {
    if (err) return send(res, 404, 'Not Found');
    const ext = path.extname(filePath).toLowerCase();
    send(res, 200, data, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
  });
}

/** /api 反向代理：把请求原样管道到后端，响应流式回传。 */
function proxyApi(req, res) {
  const upstream = http.request({
    host: API_HOST,
    port: API_PORT,
    path: req.url,
    method: req.method,
    headers: req.headers,
  }, (upRes) => {
    res.writeHead(upRes.statusCode || 502, upRes.headers);
    upRes.pipe(res);
  });
  upstream.on('error', () => send(res, 502, 'Bad Gateway'));
  req.pipe(upstream);
}

// 路由：健康检查 -> API 代理 -> 静态文件
const server = http.createServer((req, res) => {
  const urlPath = (req.url || '').split('?')[0];
  if (urlPath === '/healthz') return send(res, 200, 'ok');
  if (urlPath.startsWith('/api/')) return proxyApi(req, res);
  serveStatic(req, res);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`[frontend] serving ${ROOT} on :${PORT}, /api -> ${API_HOST}:${API_PORT}`);
});
