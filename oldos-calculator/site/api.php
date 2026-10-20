<?php
// API калькулятора OLDOS для обычного PHP-хостинга: один общий справочник для всех сотрудников,
// вход с проверкой пароля на сервере, изменять справочник может только руководитель.
declare(strict_types=1);

const WIDTHS = [240, 280, 310];
const MARKUPS = [10, 15, 40, 45, 60];
const SESSION_TTL = 7 * 24 * 3600;
const LOGIN_MAX_FAILURES = 10;
const LOGIN_WINDOW = 15 * 60;
// Файлы с данными имеют расширение .php и начинаются с exit: даже если веб-сервер
// отдаст такой файл по прямой ссылке, PHP выполнит его и ничего не покажет.
const FILE_GUARD = "<?php http_response_code(404); exit; ?>\n";

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header('Referrer-Policy: same-origin');

class HttpError extends Exception
{
    public $status;

    public function __construct(int $status, string $message)
    {
        parent::__construct($message);
        $this->status = $status;
    }
}

function respond(int $status, $body): void
{
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_UNICODE);
    exit;
}

set_exception_handler(function (Throwable $e) {
    $status = $e instanceof HttpError ? $e->status : 500;
    if ($status === 500) error_log('OLDOS: ' . $e);
    respond($status, ['error' => $status === 500 ? 'Внутренняя ошибка сервера' : $e->getMessage()]);
});

// ---------- Настройки ----------

$config = is_file(__DIR__ . '/config.php') ? require __DIR__ . '/config.php' : null;
if (!is_array($config) || ($config['director_password'] ?? '') === '' || ($config['manager_password'] ?? '') === '') {
    throw new HttpError(503, 'Сайт не настроен: задайте пароли в файле config.php');
}
define('DATA_DIR', rtrim($config['data_dir'] ?? __DIR__ . '/data', '/'));
$users = [
    ['role' => 'director', 'login' => (string)($config['director_login'] ?? 'director'), 'password' => (string)$config['director_password']],
    ['role' => 'manager', 'login' => (string)($config['manager_login'] ?? 'manager'), 'password' => (string)$config['manager_password']],
];

// ---------- Файлы с данными ----------

function ensureDataDir(): void
{
    if (!is_dir(DATA_DIR) && !@mkdir(DATA_DIR, 0700, true) && !is_dir(DATA_DIR)) {
        throw new RuntimeException('Не удалось создать папку ' . DATA_DIR);
    }
    $htaccess = DATA_DIR . '/.htaccess';
    if (!is_file($htaccess)) {
        file_put_contents($htaccess, "<IfModule mod_authz_core.c>\n    Require all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\n    Order deny,allow\n    Deny from all\n</IfModule>\n");
    }
}

function readGuarded(string $file): ?array
{
    if (!is_file($file)) return null;
    $raw = file_get_contents($file);
    $data = is_string($raw) && strncmp($raw, FILE_GUARD, strlen(FILE_GUARD)) === 0
        ? json_decode(substr($raw, strlen(FILE_GUARD)), true)
        : null;
    if (!is_array($data)) throw new RuntimeException('Повреждён файл ' . $file); // такой файл не перезаписываем
    return $data;
}

function writeGuarded(string $file, array $data): void
{
    $tmp = DATA_DIR . '/.tmp-' . bin2hex(random_bytes(6)) . '.php';
    $json = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    if ($json === false || file_put_contents($tmp, FILE_GUARD . $json) === false || !rename($tmp, $file)) {
        @unlink($tmp);
        throw new RuntimeException('Не удалось записать ' . $file);
    }
}

// Запись идёт через временный файл и rename, поэтому читать можно без блокировки;
// блокировка нужна, чтобы два одновременных сохранения не перетёрли друг друга.
function withLock(callable $fn)
{
    $handle = fopen(DATA_DIR . '/.lock', 'c');
    if (!$handle || !flock($handle, LOCK_EX)) throw new RuntimeException('Не удалось заблокировать данные');
    try {
        return $fn();
    } finally {
        flock($handle, LOCK_UN);
        fclose($handle);
    }
}

// ---------- Справочник ----------

function seedData(): array
{
    $products = [
        ['Наволочка 40×60', 240], ['Наволочка 50×50', 240], ['Наволочка 50×70', 240], ['Наволочка 60×60', 240], ['Наволочка 70×70', 240],
        ['Пододеяльник 110×140', 240], ['Пододеяльник 140×205', 240], ['Пододеяльник 150×210', 240], ['Пододеяльник 172×205', 240], ['Пододеяльник 175×210', 240], ['Пододеяльник 200×220', 280], ['Пододеяльник 220×240', 280], ['Пододеяльник 260×280', 310],
        ['Простыня 90×200', 240], ['Простыня 120×200', 240], ['Простыня 140×200', 240], ['Простыня 150×220', 240], ['Простыня 160×200', 240], ['Простыня 180×220', 240], ['Простыня 200×220', 240], ['Простыня 220×240', 240], ['Простыня 240×260', 280], ['Простыня 260×280', 310],
    ];
    $fabrics = [
        ['satin-lux', 'Сатин Люкс'], ['satin-standard', 'Сатин Стандарт'], ['polysatin-lux', 'Полисатин Люкс'], ['polysatin-stripe', 'Полисатин Полоса'], ['polycotton', 'Поликоттон'],
    ];
    $result = ['fabrics' => [], 'products' => [], 'settings' => ['labels' => 4.5, 'markups' => [10 => 10, 15 => 15, 40 => 40, 45 => 45, 60 => 60]]];
    foreach ($fabrics as [$id, $name]) $result['fabrics'][] = ['id' => $id, 'name' => $name, 'prices' => [240 => 0, 280 => 0, 310 => 0]];
    foreach ($products as $i => [$name, $width]) {
        $result['products'][] = ['id' => 'p' . ($i + 1), 'name' => $name, 'width' => $width, 'consumption10' => 0, 'consumptionSew' => 0, 'sewing' => 0, 'special' => $name === 'Пододеяльник 260×280'];
    }
    return $result;
}

function amount($v): float
{
    return is_numeric($v) && is_finite((float)$v) && (float)$v >= 0 ? (float)$v : 0.0;
}

function text($v, string $fallback): string
{
    $s = trim(is_scalar($v) ? (string)$v : '');
    $s = function_exists('mb_substr') ? mb_substr($s, 0, 200) : substr($s, 0, 200);
    return $s === '' ? $fallback : $s;
}

// Проверяет и очищает справочник, присланный руководителем (или импортированный из файла).
function normalize($input): array
{
    if (!is_array($input) || !is_array($input['fabrics'] ?? null) || !is_array($input['products'] ?? null) || !is_array($input['settings'] ?? null)) {
        throw new HttpError(400, 'Неверная структура справочника');
    }
    $ids = [];
    $uniqueId = function ($v) use (&$ids): string {
        $id = is_scalar($v) ? (string)$v : '';
        if (!preg_match('/^[\w-]{1,64}$/', $id) || isset($ids[$id])) $id = bin2hex(random_bytes(8)); // id попадает в HTML-атрибуты
        $ids[$id] = true;
        return $id;
    };
    $fabrics = [];
    foreach ($input['fabrics'] as $f) {
        if (!is_array($f)) continue;
        $prices = [];
        foreach (WIDTHS as $w) $prices[$w] = amount($f['prices'][$w] ?? null);
        $fabrics[] = ['id' => $uniqueId($f['id'] ?? null), 'name' => text($f['name'] ?? null, 'Без названия'), 'prices' => $prices];
    }
    $products = [];
    foreach ($input['products'] as $p) {
        if (!is_array($p)) continue;
        $special = ($p['special'] ?? false) === true || ($p['special'] ?? '') === 'true';
        $consumptionSew = amount($p['consumptionSew'] ?? null);
        $width = (int)($p['width'] ?? 0);
        $products[] = [
            'id' => $uniqueId($p['id'] ?? null),
            'name' => text($p['name'] ?? null, 'Без названия'),
            'width' => in_array($width, WIDTHS, true) ? $width : WIDTHS[0],
            'consumption10' => $special ? $consumptionSew * 1.1 : amount($p['consumption10'] ?? null),
            'consumptionSew' => $consumptionSew,
            'sewing' => amount($p['sewing'] ?? null),
            'special' => $special,
        ];
    }
    $markups = [];
    foreach (MARKUPS as $k) $markups[$k] = amount($input['settings']['markups'][$k] ?? null);
    return ['fabrics' => $fabrics, 'products' => $products, 'settings' => ['labels' => amount($input['settings']['labels'] ?? null), 'markups' => $markups]];
}

function storeFile(): string
{
    return DATA_DIR . '/oldos-data.php';
}

function loadStore(): array
{
    $raw = readGuarded(storeFile());
    if ($raw === null) return ['rev' => 1, 'updatedAt' => gmdate('c')] + seedData();
    return ['rev' => (int)($raw['rev'] ?? 1), 'updatedAt' => (string)($raw['updatedAt'] ?? gmdate('c'))] + normalize($raw);
}

function replaceStore(array $data): array
{
    $next = ['rev' => loadStore()['rev'] + 1, 'updatedAt' => gmdate('c')] + $data;
    writeGuarded(storeFile(), $next);
    return $next;
}

function directorView(array $s): array
{
    // Явно приводим к объектам, чтобы ключи-ширины и ключи-наценки всегда уходили в JSON как объект.
    foreach ($s['fabrics'] as &$f) $f['prices'] = (object)$f['prices'];
    unset($f);
    $s['settings']['markups'] = (object)$s['settings']['markups'];
    return $s;
}

// Менеджер получает только то, что нужно для его цены: без цен тканей за метр, норм расхода и прочих наценок.
function managerView(array $s): array
{
    $material = [];
    foreach ($s['products'] as $p) {
        $c10 = $p['special'] ? $p['consumptionSew'] * 1.1 : $p['consumption10'];
        $row = [];
        foreach ($s['fabrics'] as $f) $row[$f['id']] = $c10 * ($f['prices'][$p['width']] ?? 0);
        $material[$p['id']] = (object)$row;
    }
    return [
        'rev' => $s['rev'],
        'updatedAt' => $s['updatedAt'],
        'fabrics' => array_map(function ($f) { return ['id' => $f['id'], 'name' => $f['name']]; }, $s['fabrics']),
        'products' => array_map(function ($p) { return ['id' => $p['id'], 'name' => $p['name'], 'sewing' => $p['sewing']]; }, $s['products']),
        'material' => (object)$material,
        'settings' => ['labels' => $s['settings']['labels'], 'markups' => (object)[45 => $s['settings']['markups'][45]]],
    ];
}

// ---------- Вход ----------

function isHttps(): bool
{
    return (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
        || ($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https'
        || (string)($_SERVER['SERVER_PORT'] ?? '') === '443';
}

function cookieOptions(int $expires): array
{
    return ['expires' => $expires, 'path' => '/', 'secure' => isHttps(), 'httponly' => true, 'samesite' => 'Lax'];
}

function startSession(): void
{
    $dir = DATA_DIR . '/sessions';
    if (!is_dir($dir)) @mkdir($dir, 0700, true);
    session_save_path($dir);
    ini_set('session.gc_maxlifetime', (string)SESSION_TTL);
    ini_set('session.gc_probability', '1');
    ini_set('session.gc_divisor', '100');
    ini_set('session.use_strict_mode', '1');
    session_name('oldos_session');
    $options = cookieOptions(0);
    unset($options['expires']);
    session_set_cookie_params(['lifetime' => SESSION_TTL] + $options);
    session_start();
}

function currentRole(): ?string
{
    if (!isset($_COOKIE['oldos_session'])) return null;
    startSession();
    $role = $_SESSION['role'] ?? null;
    if ($role === null || ($_SESSION['expires'] ?? 0) < time()) {
        session_destroy();
        setcookie('oldos_session', '', cookieOptions(time() - 3600));
        return null;
    }
    session_write_close(); // дальше сессия не меняется: не держим её блокировку
    return $role;
}

function throttleFile(): string
{
    return DATA_DIR . '/login-failures.php';
}

function loginBlocked(string $ip): bool
{
    $f = (readGuarded(throttleFile()) ?? [])[$ip] ?? null;
    return $f && $f['count'] >= LOGIN_MAX_FAILURES && time() - $f['since'] < LOGIN_WINDOW;
}

function recordLogin(string $ip, bool $ok): void
{
    withLock(function () use ($ip, $ok) {
        $all = readGuarded(throttleFile()) ?? [];
        $now = time();
        foreach ($all as $key => $f) if ($now - $f['since'] >= LOGIN_WINDOW) unset($all[$key]);
        if ($ok) unset($all[$ip]);
        elseif (isset($all[$ip])) $all[$ip]['count']++;
        else $all[$ip] = ['count' => 1, 'since' => $now];
        writeGuarded(throttleFile(), $all);
    });
}

// ---------- Запрос ----------

ensureDataDir();
$action = (string)($_GET['action'] ?? '');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$body = [];
if ($method === 'POST') {
    // JSON нельзя отправить с чужого сайта без разрешения CORS — это защищает от подделки запросов.
    if (stripos($_SERVER['CONTENT_TYPE'] ?? '', 'application/json') !== 0) throw new HttpError(415, 'Ожидается JSON');
    $raw = (string)file_get_contents('php://input');
    if (strlen($raw) > 2000000) throw new HttpError(413, 'Слишком большой запрос');
    $body = json_decode($raw === '' ? '{}' : $raw, true);
    if (!is_array($body)) throw new HttpError(400, 'Некорректный JSON');
}
$requireMethod = function (string $expected) use ($method) {
    if ($method !== $expected) throw new HttpError(405, 'Неверный метод запроса');
};

if ($action === 'login') {
    $requireMethod('POST');
    $ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
    if (loginBlocked($ip)) throw new HttpError(429, 'Слишком много неудачных попыток. Попробуйте через 15 минут.');
    $login = trim(is_string($body['login'] ?? null) ? $body['login'] : '');
    $password = is_string($body['password'] ?? null) ? $body['password'] : '';
    $role = null;
    foreach ($users as $u) {
        if (hash_equals($u['login'], $login) && hash_equals($u['password'], $password)) {
            $role = $u['role'];
            break;
        }
    }
    recordLogin($ip, $role !== null);
    if ($role === null) throw new HttpError(401, 'Неверный логин или пароль');
    startSession();
    session_regenerate_id(true);
    $_SESSION = ['role' => $role, 'expires' => time() + SESSION_TTL];
    respond(200, ['role' => $role]);
}

if ($action === 'logout') {
    $requireMethod('POST');
    if (isset($_COOKIE['oldos_session'])) {
        startSession();
        $_SESSION = [];
        session_destroy();
    }
    setcookie('oldos_session', '', cookieOptions(time() - 3600));
    respond(200, ['ok' => true]);
}

$role = currentRole();
if ($role === null) throw new HttpError(401, 'Требуется вход');

if ($action === 'session') respond(200, ['role' => $role]);
if ($action === 'version') respond(200, ['rev' => (int)((readGuarded(storeFile()) ?? [])['rev'] ?? 1)]);
if ($action === 'data') {
    $store = loadStore();
    respond(200, $role === 'director' ? directorView($store) : managerView($store));
}

if ($action !== 'save' && $action !== 'reset') throw new HttpError(404, 'Не найдено');
$requireMethod('POST');
if ($role !== 'director') throw new HttpError(403, 'Изменять справочник может только руководитель');

$store = withLock(function () use ($action, $body) {
    if ($action === 'reset') return replaceStore(seedData());
    // Защита от затирания: сохранять можно только поверх той версии, которую руководитель видел.
    if ((int)($body['rev'] ?? 0) !== loadStore()['rev']) {
        throw new HttpError(409, 'Справочник уже изменён в другом окне. Показаны актуальные данные — повторите правку.');
    }
    return replaceStore(normalize($body['data'] ?? null));
});
respond(200, directorView($store));
