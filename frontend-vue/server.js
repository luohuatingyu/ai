/* Vue 前端容器服务：静态托管 Vite 构建产物 + /api 反向代理到后端（零依赖，仅 Node 内置模块）。
 *
 * 设计要点（与旧版 frontend/server.js 保持同一模式）：
 * - 静态文件来自构建期产物 dist/（Vue 3 + Vite，带 hash 的 assets 可长缓存）；
 * - SPA 回退：未命中文件的路由一律返回 index.html（Vue Router history 模式）；
 * - /api/* 与 /ws 原样转发到后端 trainer 容器（流式管道 + WebSocket 升级）；
 * - /healthz 供容器健康检查；
 * - 页面与 API 同源（同一端口），无需 CORS 配置。
 */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = process.env.FRONTEND_ROOT || '/srv/dist';      // Vite 构建产物目录
const PORT = Number(process.env.FRONTEND_PORT || 8000);     // 监听端口
const API_HOST = process.env.API_PROXY_HOST || 'trainer';   // 后端容器名
const API_PORT = Number(process.env.API_PROXY_PORT || 8000);

// 常用静态类型映射（未命中回退 application/octet-stream）
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
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

/** 静态文件服务：未命中回退 index.html（SPA 路由），防目录穿越。
 *  缓存策略：HTML 一律 no-cache（应用入口必须每次校验，否则前端更新不生效）；
 *  /assets/ 下带内容 hash 的产物允许长缓存（immutable）。 */
function serveStatic(req, res) {
  let urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
  if (urlPath === '/') urlPath = '/index.html';
  const root = path.normalize(ROOT);
  const filePath = path.normalize(path.join(root, urlPath));
  if (filePath !== path.join(root, 'index.html') && !filePath.startsWith(root + path.sep)) {
    return send(res, 403, 'Forbidden');
  }
  fs.stat(filePath, (statErr, stat) => {
    const target = (statErr || !stat.isFile()) ? path.join(root, 'index.html') : filePath;
    const isFallback = target === path.join(root, 'index.html') && urlPath !== '/index.html';
    fs.readFile(target, (err, data) => {
      if (err) return send(res, 404, 'Not Found');
      const ext = path.extname(target).toLowerCase();
      const cache = ext === '.html'
        ? 'no-cache, no-store, must-revalidate'   // 应用入口：每次校验，前端改动即时生效
        : (urlPath.startsWith('/assets/')
          ? 'public, max-age=31536000, immutable' // hash 产物：长缓存
          : 'public, max-age=3600');
      send(res, 200, data, {
        'Content-Type': MIME[ext] || 'application/octet-stream',
        'Cache-Control': cache,
      });
      void isFallback;
    });
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

/** WebSocket 升级代理：双向管道转发（Notebook 实时输出）。 */
function proxyUpgrade(req, socket, head) {
  const upstream = http.request({
    host: API_HOST,
    port: API_PORT,
    path: req.url,
    method: req.method,
    headers: req.headers,
  });
  upstream.on('upgrade', (upRes, upSocket, upHead) => {
    const lines = [`HTTP/${upRes.httpVersion} ${upRes.statusCode} ${upRes.statusMessage}`];
    for (const [key, value] of Object.entries(upRes.headers)) {
      lines.push(`${key}: ${value}`);
    }
    socket.write(lines.join('\r\n') + '\r\n\r\n');
    if (upHead && upHead.length) socket.write(upHead);
    upSocket.pipe(socket);
    socket.pipe(upSocket);
  });
  upstream.on('error', () => socket.destroy());
  upstream.end();
  socket.on('error', () => upstream.destroy());
  if (head && head.length) {
    // 首包数据需在升级完成后转发；Node http.request upgrade 场景下由 upSocket 接续
    void head;
  }
}

// 路由：健康检查 -> API 代理 -> 静态文件（SPA 回退）
const server = http.createServer((req, res) => {
  const urlPath = (req.url || '').split('?')[0];
  if (urlPath === '/healthz') return send(res, 200, 'ok');
  if (urlPath.startsWith('/api/')) return proxyApi(req, res);
  serveStatic(req, res);
});

server.on('upgrade', (req, socket, head) => {
  const urlPath = (req.url || '').split('?')[0];
  if (urlPath.startsWith('/api/') || urlPath.startsWith('/ws')) {
    return proxyUpgrade(req, socket, head);
  }
  socket.destroy();
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`[frontend] serving ${ROOT} on :${PORT}, /api+ws -> ${API_HOST}:${API_PORT}`);
});