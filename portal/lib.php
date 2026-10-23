<?php
/**
 * Сайт для менеджеров (поддомен на обычном хостинге с PHP и MySQL).
 *
 * Данные приходят с компьютера, где работает основное приложение: оно после каждого прогона и
 * раз в несколько минут отправляет сюда тендеры, менеджеров и уведомления (api.php) и забирает
 * отметки менеджеров — «Мой статус», заметки, «Подходит / не подходит».
 * Пароли менеджеров — те же, что заданы администратором в приложении (PBKDF2-SHA256).
 * Нужен PHP 7.4+ с PDO (MySQL; SQLite — для тестов).
 */

declare(strict_types=1);

const STATUSES = ['новый', 'в работе', 'подаём заявку', 'подали', 'не интересно'];
const RELEVANCE_LABELS = ['relevant' => 'Релевантный', 'review' => 'На проверку', 'rejected' => 'Отклонён'];
const VERDICT_LABELS = ['yes' => 'подходит', 'no' => 'не подходит'];
const MAX_FAILURES = 10;
const FAILURE_WINDOW = 900;
const PER_PAGE = 50;

function config(): array
{
    static $config = null;
    if ($config === null) {
        $path = getenv('PORTAL_CONFIG') ?: __DIR__ . '/config.php';
        if (!is_file($path)) {
            http_response_code(500);
            exit('Не найден config.php — скопируйте config.example.php в config.php и заполните.');
        }
        $config = require $path;
        date_default_timezone_set($config['timezone'] ?? 'Europe/Moscow');
    }
    return $config;
}

function db(): PDO
{
    static $pdo = null;
    if ($pdo === null) {
        $c = config();
        try {
            $pdo = new PDO($c['db_dsn'], $c['db_user'] ?? null, $c['db_password'] ?? null, [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            ]);
            ensure_schema($pdo);
        } catch (PDOException $e) {
            // Понятная причина вместо пустой «ошибки 500» (пароль в сообщение не попадает).
            http_response_code(500);
            $text = 'Нет подключения к базе — проверьте имя базы, пользователя и пароль в config.php: ' . $e->getMessage();
            if (basename($_SERVER['SCRIPT_NAME'] ?? '') === 'api.php') {
                header('Content-Type: application/json; charset=utf-8');
                exit(json_encode(['error' => $text], JSON_UNESCAPED_UNICODE));
            }
            header('Content-Type: text/plain; charset=utf-8');
            exit($text);
        }
    }
    return $pdo;
}

function ensure_schema(PDO $pdo): void
{
    $mysql = $pdo->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
    $auto = $mysql ? 'INT AUTO_INCREMENT PRIMARY KEY' : 'INTEGER PRIMARY KEY AUTOINCREMENT';
    $tail = $mysql ? ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4' : '';
    $tables = [
        "CREATE TABLE IF NOT EXISTS portal_tenders (
            id INT PRIMARY KEY, data MEDIUMTEXT NOT NULL, search TEXT NOT NULL,
            relevance VARCHAR(20) NOT NULL, effective VARCHAR(20) NOT NULL,
            deadline VARCHAR(32), published VARCHAR(32), first_seen VARCHAR(32), nmck DECIMAL(18,2),
            user_status VARCHAR(40) NOT NULL, notes TEXT NOT NULL, verdict VARCHAR(10),
            status_at VARCHAR(32), notes_at VARCHAR(32), verdict_at VARCHAR(32), updated_by VARCHAR(100))",
        "CREATE TABLE IF NOT EXISTS portal_managers (
            login VARCHAR(100) PRIMARY KEY, name VARCHAR(255) NOT NULL, password_hash VARCHAR(255) NOT NULL,
            active INT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS portal_manager_state (
            login VARCHAR(100) PRIMARY KEY, notifications_seen_at VARCHAR(32))",
        "CREATE TABLE IF NOT EXISTS portal_views (
            login VARCHAR(100) NOT NULL, tender_id INT NOT NULL, PRIMARY KEY (login, tender_id))",
        "CREATE TABLE IF NOT EXISTS portal_changes (
            id $auto, tender_id INT NOT NULL, login VARCHAR(100) NOT NULL, field VARCHAR(20) NOT NULL,
            value TEXT, created_at VARCHAR(32) NOT NULL)",
        "CREATE TABLE IF NOT EXISTS portal_notifications (
            id INT PRIMARY KEY, title TEXT NOT NULL, body MEDIUMTEXT NOT NULL, created_at VARCHAR(32) NOT NULL)",
        "CREATE TABLE IF NOT EXISTS portal_login_failures (ip VARCHAR(64) NOT NULL, at INT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS portal_meta (k VARCHAR(50) PRIMARY KEY, v TEXT)",
    ];
    foreach ($tables as $sql) {
        if (!$mysql) {
            $sql = str_replace('MEDIUMTEXT', 'TEXT', $sql);
        }
        $pdo->exec($sql . $tail);
    }
}

function now_iso(): string
{
    return gmdate('Y-m-d\TH:i:s\Z');
}

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function fmt_dt(?string $iso, string $format = 'd.m.Y H:i'): string
{
    if (!$iso) {
        return '—';
    }
    try {
        return (new DateTimeImmutable($iso))->setTimezone(new DateTimeZone(date_default_timezone_get()))->format($format);
    } catch (Exception $e) {
        return '—';
    }
}

function fmt_money($value): string
{
    if ($value === null || $value === '') {
        return '—';
    }
    return number_format((float)$value, 2, ',', ' ') . ' ₽';
}

function meta_get(string $key): ?string
{
    $st = db()->prepare('SELECT v FROM portal_meta WHERE k = ?');
    $st->execute([$key]);
    $v = $st->fetchColumn();
    return $v === false ? null : (string)$v;
}

function meta_set(string $key, string $value): void
{
    db()->prepare('REPLACE INTO portal_meta (k, v) VALUES (?, ?)')->execute([$key, $value]);
}

// ----------------------------------------------------------------------------- вход

function start_session(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
        || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    session_name('VLMPORTAL');
    session_set_cookie_params([
        'lifetime' => 30 * 86400,
        'path' => '/',
        'secure' => $https,
        'httponly' => true,
        'samesite' => 'Lax',
    ]);
    ini_set('session.gc_maxlifetime', (string)(30 * 86400));
    session_start();
}

function verify_password(string $password, string $stored): bool
{
    $parts = explode('$', $stored);
    if (count($parts) !== 4 || $parts[0] !== 'pbkdf2_sha256' || !ctype_xdigit($parts[2])) {
        return false;
    }
    $check = hash_pbkdf2('sha256', $password, hex2bin($parts[2]), (int)$parts[1], 64);
    return hash_equals($parts[3], $check);
}

function password_stamp(string $hash): string
{
    return substr(hash('sha256', $hash), 0, 16);
}

function client_ip(): string
{
    return substr((string)($_SERVER['REMOTE_ADDR'] ?? '?'), 0, 64);
}

function login_blocked(): bool
{
    $pdo = db();
    $pdo->prepare('DELETE FROM portal_login_failures WHERE at < ?')->execute([time() - FAILURE_WINDOW]);
    $st = $pdo->prepare('SELECT COUNT(*) FROM portal_login_failures WHERE ip = ?');
    $st->execute([client_ip()]);
    return (int)$st->fetchColumn() >= MAX_FAILURES;
}

function try_login(string $login, string $password): ?array
{
    $st = db()->prepare('SELECT * FROM portal_managers WHERE LOWER(login) = ? AND active = 1');
    $st->execute([mb_strtolower(trim($login))]);
    $m = $st->fetch();
    if (!$m || !verify_password($password, $m['password_hash'])) {
        db()->prepare('INSERT INTO portal_login_failures (ip, at) VALUES (?, ?)')->execute([client_ip(), time()]);
        return null;
    }
    db()->prepare('DELETE FROM portal_login_failures WHERE ip = ?')->execute([client_ip()]);
    session_regenerate_id(true);
    $_SESSION['login'] = $m['login'];
    $_SESSION['stamp'] = password_stamp($m['password_hash']);
    return $m;
}

/** Текущий менеджер; при смене пароля или закрытии доступа сессия перестаёт действовать. */
function current_manager(): ?array
{
    start_session();
    if (empty($_SESSION['login'])) {
        return null;
    }
    $st = db()->prepare('SELECT * FROM portal_managers WHERE login = ? AND active = 1');
    $st->execute([$_SESSION['login']]);
    $m = $st->fetch();
    if (!$m || !hash_equals((string)($_SESSION['stamp'] ?? ''), password_stamp($m['password_hash']))) {
        $_SESSION = [];
        return null;
    }
    return $m;
}

function require_manager(): array
{
    $m = current_manager();
    if ($m === null) {
        header('Location: login.php?next=' . urlencode($_SERVER['REQUEST_URI'] ?? 'index.php'));
        exit;
    }
    return $m;
}

function csrf_token(): string
{
    start_session();
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(16));
    }
    return $_SESSION['csrf'];
}

function check_csrf(): void
{
    start_session();
    if (!hash_equals((string)($_SESSION['csrf'] ?? ''), (string)($_POST['csrf'] ?? ''))) {
        http_response_code(400);
        exit('Форма устарела — обновите страницу и повторите.');
    }
}

function safe_back(string $back): string
{
    return preg_match('#^[a-z]+\.php(\?[^\s]*)?$#', $back) ? $back : 'index.php';
}

// ----------------------------------------------------------------------------- данные

function tender(int $id): ?array
{
    $st = db()->prepare('SELECT * FROM portal_tenders WHERE id = ?');
    $st->execute([$id]);
    $row = $st->fetch();
    if (!$row) {
        return null;
    }
    $row['d'] = json_decode($row['data'], true) ?: [];
    return $row;
}

function is_viewed(string $login, int $tenderId): bool
{
    $st = db()->prepare('SELECT 1 FROM portal_views WHERE login = ? AND tender_id = ?');
    $st->execute([$login, $tenderId]);
    return (bool)$st->fetchColumn();
}

function mark_viewed(string $login, int $tenderId): void
{
    if (!is_viewed($login, $tenderId)) {
        db()->prepare('INSERT INTO portal_views (login, tender_id) VALUES (?, ?)')->execute([$login, $tenderId]);
    }
}

/** Отметка менеджера: сразу видна другим менеджерам и уходит в приложение при синхронизации. */
function record_change(array $manager, int $tenderId, string $field, ?string $value): void
{
    $t = tender($tenderId);
    if ($t === null) {
        return;
    }
    $now = now_iso();
    $pdo = db();
    if ($field === 'user_status') {
        if (!in_array($value, STATUSES, true)) {
            return;
        }
        $pdo->prepare('UPDATE portal_tenders SET user_status = ?, status_at = ?, updated_by = ? WHERE id = ?')
            ->execute([$value, $now, $manager['login'], $tenderId]);
    } elseif ($field === 'notes') {
        $value = mb_substr((string)$value, 0, 10000);
        $pdo->prepare('UPDATE portal_tenders SET notes = ?, notes_at = ?, updated_by = ? WHERE id = ?')
            ->execute([$value, $now, $manager['login'], $tenderId]);
    } elseif ($field === 'verdict') {
        $value = in_array($value, ['yes', 'no'], true) ? $value : null;
        $effective = $value === 'yes' ? 'relevant' : ($value === 'no' ? 'rejected' : $t['relevance']);
        $pdo->prepare('UPDATE portal_tenders SET verdict = ?, verdict_at = ?, effective = ?, updated_by = ? WHERE id = ?')
            ->execute([$value, $now, $effective, $manager['login'], $tenderId]);
    } else {
        return;
    }
    $pdo->prepare('INSERT INTO portal_changes (tender_id, login, field, value, created_at) VALUES (?, ?, ?, ?, ?)')
        ->execute([$tenderId, $manager['login'], $field, $value, $now]);
}

function unread_count(string $login): int
{
    $st = db()->prepare('SELECT notifications_seen_at FROM portal_manager_state WHERE login = ?');
    $st->execute([$login]);
    $seen = $st->fetchColumn();
    if ($seen) {
        $st = db()->prepare('SELECT COUNT(*) FROM portal_notifications WHERE created_at > ?');
        $st->execute([$seen]);
    } else {
        $st = db()->query('SELECT COUNT(*) FROM portal_notifications');
    }
    return (int)$st->fetchColumn();
}

function new_count(string $login): int
{
    $st = db()->prepare(
        "SELECT COUNT(*) FROM portal_tenders t WHERE t.effective IN ('relevant', 'review') AND t.first_seen >= ?
         AND NOT EXISTS (SELECT 1 FROM portal_views v WHERE v.login = ? AND v.tender_id = t.id)"
    );
    $st->execute([gmdate('Y-m-d\TH:i:s\Z', time() - 14 * 86400), $login]);
    return (int)$st->fetchColumn();
}

// ----------------------------------------------------------------------------- разметка

function page_header(string $title, ?array $manager): void
{
    $self = basename($_SERVER['SCRIPT_NAME'] ?? '');
    echo '<!doctype html><html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>', h($title), ' · Velmorium</title><link rel="stylesheet" href="style.css"></head><body>',
        '<header class="topbar"><a class="brand" href="index.php">Velmorium · тендеры</a>';
    if ($manager) {
        $new = new_count($manager['login']);
        $unread = unread_count($manager['login']);
        echo '<nav><a href="index.php" class="', in_array($self, ['index.php', 'tender.php'], true) ? 'active' : '', '">Тендеры',
            $new ? ' <span class="badge badge-relevant">' . $new . '</span>' : '', '</a>',
            '<a href="notifications.php" class="', $self === 'notifications.php' ? 'active' : '', '">Уведомления',
            $unread ? ' <span class="badge">' . $unread . '</span>' : '', '</a></nav>',
            '<div class="userbox">', h($manager['name'] ?: $manager['login']),
            ' <form method="post" action="logout.php"><input type="hidden" name="csrf" value="', h(csrf_token()), '">',
            '<button type="submit">Выйти</button></form></div>';
    }
    echo '</header><main>';
    $synced = meta_get('last_push_at');
    if ($manager && $synced) {
        echo '<p class="small muted sync-note">Данные обновлены ', h(fmt_dt($synced)), '</p>';
    }
}

function page_footer(): void
{
    echo '</main></body></html>';
}

function verdict_buttons(array $t, string $back): string
{
    $out = '<form method="post" action="action.php" class="verdict">'
        . '<input type="hidden" name="csrf" value="' . h(csrf_token()) . '">'
        . '<input type="hidden" name="tender_id" value="' . (int)$t['id'] . '">'
        . '<input type="hidden" name="field" value="verdict">'
        . '<input type="hidden" name="back" value="' . h($back) . '">';
    foreach (['yes' => '✓ Да', 'no' => '✗ Нет'] as $v => $label) {
        $on = $t['verdict'] === $v;
        $out .= '<button type="submit" name="value" value="' . ($on ? '' : $v) . '" class="' . $v . ($on ? ' on' : '')
            . '" title="' . ($v === 'yes' ? 'Подходит' : 'Не подходит') . '">' . $label . '</button>';
    }
    return $out . '</form>';
}
