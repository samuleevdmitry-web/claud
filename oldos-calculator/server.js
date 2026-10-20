'use strict';
// Сервер калькулятора OLDOS: один общий справочник для всех сотрудников,
// вход с проверкой пароля на сервере, мгновенная рассылка изменений (Server-Sent Events).
// Без внешних зависимостей: нужен только Node.js 18+.
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PORT = Number(process.env.PORT) || 3000;
const DATA_FILE = path.resolve(process.env.DATA_FILE || path.join(__dirname, 'data', 'data.json'));
const INDEX_HTML = fs.readFileSync(path.join(__dirname, 'public', 'index.html'));
const COOKIE = 'oldos_session';
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const LOGIN_MAX_FAILURES = 10;
const LOGIN_WINDOW_MS = 15 * 60 * 1000;
const WIDTHS = [240, 280, 310];
const MARKUPS = [10, 15, 40, 45, 60];

const USERS = [
  { role: 'director', login: process.env.DIRECTOR_LOGIN || 'director', password: process.env.DIRECTOR_PASSWORD, env: 'DIRECTOR_PASSWORD' },
  { role: 'manager', login: process.env.MANAGER_LOGIN || 'manager', password: process.env.MANAGER_PASSWORD, env: 'MANAGER_PASSWORD' },
];
const missing = USERS.filter(u => !u.password).map(u => u.env);
if (missing.length) {
  console.error(`Задайте пароли в переменных окружения: ${missing.join(', ')}`);
  process.exit(1);
}

// ---------- Справочник ----------

const PRODUCT_NAMES = [
  ['Наволочка 40×60', 240], ['Наволочка 50×50', 240], ['Наволочка 50×70', 240], ['Наволочка 60×60', 240], ['Наволочка 70×70', 240],
  ['Пододеяльник 110×140', 240], ['Пододеяльник 140×205', 240], ['Пододеяльник 150×210', 240], ['Пододеяльник 172×205', 240], ['Пододеяльник 175×210', 240], ['Пододеяльник 200×220', 280], ['Пододеяльник 220×240', 280], ['Пододеяльник 260×280', 310],
  ['Простыня 90×200', 240], ['Простыня 120×200', 240], ['Простыня 140×200', 240], ['Простыня 150×220', 240], ['Простыня 160×200', 240], ['Простыня 180×220', 240], ['Простыня 200×220', 240], ['Простыня 220×240', 240], ['Простыня 240×260', 280], ['Простыня 260×280', 310],
];
const FABRICS = [
  ['satin-lux', 'Сатин Люкс'], ['satin-standard', 'Сатин Стандарт'], ['polysatin-lux', 'Полисатин Люкс'], ['polysatin-stripe', 'Полисатин Полоса'], ['polycotton', 'Поликоттон'],
];

function seedData() {
  return {
    fabrics: FABRICS.map(([id, name]) => ({ id, name, prices: { 240: 0, 280: 0, 310: 0 } })),
    products: PRODUCT_NAMES.map(([name, width], i) => ({ id: 'p' + (i + 1), name, width, consumption10: 0, consumptionSew: 0, sewing: 0, special: name === 'Пододеяльник 260×280' })),
    settings: { labels: 4.5, markups: { 10: 10, 15: 15, 40: 40, 45: 45, 60: 60 } },
  };
}

function httpError(status, message) {
  return Object.assign(new Error(message), { status });
}
function amount(v) {
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}
function text(v, fallback) {
  return String(v ?? '').trim().slice(0, 200) || fallback;
}

// Проверяет и очищает справочник, присланный руководителем (или импортированный из файла).
function normalize(input) {
  if (!input || !Array.isArray(input.fabrics) || !Array.isArray(input.products) || !input.settings || typeof input.settings !== 'object') {
    throw httpError(400, 'Неверная структура справочника');
  }
  const ids = new Set();
  const uniqueId = v => {
    let id = String(v ?? '');
    if (!/^[\w-]{1,64}$/.test(id) || ids.has(id)) id = crypto.randomUUID(); // id попадает в HTML-атрибуты
    ids.add(id);
    return id;
  };
  const isObject = x => x && typeof x === 'object';
  const fabrics = input.fabrics.filter(isObject).map(f => ({
    id: uniqueId(f.id),
    name: text(f.name, 'Без названия'),
    prices: Object.fromEntries(WIDTHS.map(w => [w, amount(f.prices?.[w])])),
  }));
  const products = input.products.filter(isObject).map(p => {
    const special = p.special === true || p.special === 'true';
    const consumptionSew = amount(p.consumptionSew);
    return {
      id: uniqueId(p.id),
      name: text(p.name, 'Без названия'),
      width: WIDTHS.includes(Number(p.width)) ? Number(p.width) : WIDTHS[0],
      consumption10: special ? consumptionSew * 1.1 : amount(p.consumption10),
      consumptionSew,
      sewing: amount(p.sewing),
      special,
    };
  });
  const settings = {
    labels: amount(input.settings.labels),
    markups: Object.fromEntries(MARKUPS.map(k => [k, amount(input.settings.markups?.[k])])),
  };
  return { fabrics, products, settings };
}

// Менеджер получает только то, что нужно для цены +45%: без цен тканей за метр, норм расхода и прочих наценок.
function managerView() {
  const material = {};
  for (const p of store.products) {
    const c10 = p.special ? p.consumptionSew * 1.1 : p.consumption10;
    material[p.id] = Object.fromEntries(store.fabrics.map(f => [f.id, c10 * (f.prices[p.width] || 0)]));
  }
  return {
    rev: store.rev,
    updatedAt: store.updatedAt,
    fabrics: store.fabrics.map(({ id, name }) => ({ id, name })),
    products: store.products.map(({ id, name, sewing }) => ({ id, name, sewing })),
    material,
    settings: { labels: store.settings.labels, markups: { 45: store.settings.markups[45] } },
  };
}

function writeStore(data) {
  fs.mkdirSync(path.dirname(DATA_FILE), { recursive: true });
  const tmp = DATA_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
  fs.renameSync(tmp, DATA_FILE);
}

function loadStore() {
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
  } catch (e) {
    if (e.code !== 'ENOENT') throw e; // повреждённый файл не перезаписываем
    const fresh = { rev: 1, updatedAt: new Date().toISOString(), ...seedData() };
    writeStore(fresh);
    return fresh;
  }
  return { rev: Number(raw.rev) || 1, updatedAt: raw.updatedAt || new Date().toISOString(), ...normalize(raw) };
}

function replaceStore(data) {
  const next = { rev: store.rev + 1, updatedAt: new Date().toISOString(), ...data };
  writeStore(next);
  store = next;
}

let store = loadStore();

// ---------- Сессии и вход ----------

const sessions = new Map(); // token -> { role, expires }
const failures = new Map(); // ip -> { count, since }

function readCookie(req, name) {
  for (const part of (req.headers.cookie || '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
}

function sessionCookie(req, value, maxAgeSec) {
  const secure = req.socket.encrypted || req.headers['x-forwarded-proto'] === 'https' ? '; Secure' : '';
  return `${COOKIE}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAgeSec}${secure}`;
}

function getSession(req) {
  const token = readCookie(req, COOKIE);
  const session = token && sessions.get(token);
  if (!session) return null;
  if (session.expires < Date.now()) {
    sessions.delete(token);
    return null;
  }
  return { token, role: session.role };
}

function endSession(token) {
  sessions.delete(token);
  closeStreams(token);
}

function safeEqual(a, b) {
  const ha = crypto.createHash('sha256').update(String(a)).digest();
  const hb = crypto.createHash('sha256').update(String(b)).digest();
  return crypto.timingSafeEqual(ha, hb);
}

function clientIp(req) {
  return String(req.headers['x-forwarded-for'] || '').split(',')[0].trim() || req.socket.remoteAddress;
}

async function login(req, res) {
  const ip = clientIp(req);
  const f = failures.get(ip);
  if (f && f.count >= LOGIN_MAX_FAILURES && Date.now() - f.since < LOGIN_WINDOW_MS) {
    return sendJson(res, 429, { error: 'Слишком много неудачных попыток. Попробуйте через 15 минут.' });
  }
  const body = await readJson(req, 10_000);
  const user = USERS.find(u => safeEqual(u.login, String(body.login ?? '').trim()) && safeEqual(u.password, String(body.password ?? '')));
  if (!user) {
    if (!f || Date.now() - f.since >= LOGIN_WINDOW_MS) failures.set(ip, { count: 1, since: Date.now() });
    else f.count++;
    return sendJson(res, 401, { error: 'Неверный логин или пароль' });
  }
  failures.delete(ip);
  const previous = readCookie(req, COOKIE);
  if (previous) endSession(previous);
  const token = crypto.randomBytes(32).toString('base64url');
  sessions.set(token, { role: user.role, expires: Date.now() + SESSION_TTL_MS });
  sendJson(res, 200, { role: user.role }, { 'Set-Cookie': sessionCookie(req, token, SESSION_TTL_MS / 1000) });
}

// ---------- Мгновенные обновления (SSE) ----------

const streams = new Map(); // response -> session token

function openStream(req, res, session) {
  res.writeHead(200, { ...BASE_HEADERS, 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no' });
  // Сразу сообщаем текущую версию: клиент, переживший обрыв связи, поймёт, что пропустил изменения.
  res.write(`retry: 5000\nevent: update\ndata: ${JSON.stringify({ rev: store.rev })}\n\n`);
  streams.set(res, session.token);
  req.on('close', () => streams.delete(res));
}

function broadcast() {
  const message = `event: update\ndata: ${JSON.stringify({ rev: store.rev })}\n\n`;
  for (const res of streams.keys()) res.write(message);
}

function closeStreams(token) {
  for (const [res, t] of streams) {
    if (t === token) {
      streams.delete(res);
      res.end();
    }
  }
}

setInterval(() => {
  const now = Date.now();
  for (const [token, s] of sessions) if (s.expires < now) endSession(token);
  for (const [ip, f] of failures) if (now - f.since >= LOGIN_WINDOW_MS) failures.delete(ip);
  for (const res of streams.keys()) res.write(': ping\n\n'); // не даём прокси закрыть «тихое» соединение
}, 25_000).unref();

// ---------- HTTP ----------

const BASE_HEADERS = { 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin' };

function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, { ...BASE_HEADERS, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...headers });
  res.end(JSON.stringify(body));
}

function readJson(req, limit = 2_000_000) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let chunks = [];
    req.on('data', c => {
      size += c.length;
      if (size > limit) chunks = null;
      else chunks?.push(c);
    });
    req.on('end', () => {
      if (!chunks) return reject(httpError(413, 'Слишком большой запрос'));
      try {
        const value = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
        resolve(value && typeof value === 'object' ? value : {});
      } catch {
        reject(httpError(400, 'Некорректный JSON'));
      }
    });
    req.on('error', reject);
  });
}

async function handle(req, res) {
  const { pathname } = new URL(req.url, 'http://localhost');
  if (req.method === 'GET' && (pathname === '/' || pathname === '/index.html')) {
    res.writeHead(200, { ...BASE_HEADERS, 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache' });
    return res.end(INDEX_HTML);
  }
  if (!pathname.startsWith('/api/')) {
    res.writeHead(404, { ...BASE_HEADERS, 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end('Страница не найдена');
  }
  // Запросы с JSON нельзя отправить с чужого сайта без разрешения CORS — это защищает от подделки запросов.
  if (req.method !== 'GET' && !String(req.headers['content-type'] || '').startsWith('application/json')) {
    return sendJson(res, 415, { error: 'Ожидается JSON' });
  }
  if (req.method === 'POST' && pathname === '/api/login') return login(req, res);
  if (req.method === 'POST' && pathname === '/api/logout') {
    const token = readCookie(req, COOKIE);
    if (token) endSession(token);
    return sendJson(res, 200, { ok: true }, { 'Set-Cookie': sessionCookie(req, '', 0) });
  }

  const session = getSession(req);
  if (!session) return sendJson(res, 401, { error: 'Требуется вход' });
  if (req.method === 'GET' && pathname === '/api/session') return sendJson(res, 200, { role: session.role });
  if (req.method === 'GET' && pathname === '/api/data') return sendJson(res, 200, session.role === 'director' ? store : managerView());
  if (req.method === 'GET' && pathname === '/api/events') return openStream(req, res, session);

  const isPut = req.method === 'PUT' && pathname === '/api/data';
  const isReset = req.method === 'POST' && pathname === '/api/reset';
  if (!isPut && !isReset) return sendJson(res, 404, { error: 'Не найдено' });
  if (session.role !== 'director') return sendJson(res, 403, { error: 'Изменять справочник может только руководитель' });

  const body = await readJson(req);
  if (isPut) {
    // Защита от затирания: сохранять можно только поверх той версии, которую руководитель видел.
    if (Number(body.rev) !== store.rev) {
      return sendJson(res, 409, { error: 'Справочник уже изменён в другом окне. Показаны актуальные данные — повторите правку.' });
    }
    replaceStore(normalize(body.data));
  } else {
    replaceStore(seedData());
  }
  sendJson(res, 200, store);
  broadcast();
}

const server = http.createServer((req, res) => {
  handle(req, res).catch(e => {
    const status = e.status || 500;
    if (status === 500) console.error(e);
    if (res.headersSent) return res.end();
    sendJson(res, status, { error: status === 500 ? 'Внутренняя ошибка сервера' : e.message });
  });
});

server.listen(PORT, () => console.log(`OLDOS: http://localhost:${PORT} (справочник: ${DATA_FILE})`));
