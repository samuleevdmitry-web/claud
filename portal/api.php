<?php
/**
 * Обмен с приложением на компьютере (только POST, JSON, ключ sync_token из config.php).
 *
 * action=ping     — проверка связи;
 * action=changes  — отметки менеджеров после id=after (статус, заметки, «подходит / не подходит»);
 * action=push     — снимок для менеджеров частями: all_ids (какие тендеры оставить), tenders (порция),
 *                   managers, notifications, ack (до какого id отметки уже забраны), final (последняя порция).
 */
require __DIR__ . '/lib.php';
header('Content-Type: application/json; charset=utf-8');

function reply(array $data, int $code = 200): void
{
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    reply(['error' => 'только POST'], 405);
}
$req = json_decode((string)file_get_contents('php://input'), true);
$token = (string)(config()['sync_token'] ?? '');
if (strlen($token) < 16) {
    reply(['error' => 'в config.php не задан sync_token (не короче 16 символов)'], 500);
}
if (!is_array($req) || !hash_equals($token, (string)($req['token'] ?? ''))) {
    reply(['error' => 'неверный ключ обмена'], 403);
}
$pdo = db();
$action = (string)($req['action'] ?? '');

if ($action === 'ping') {
    reply(['ok' => true, 'version' => 1, 'php' => PHP_VERSION, 'driver' => $pdo->getAttribute(PDO::ATTR_DRIVER_NAME)]);
}

if ($action === 'changes') {
    $st = $pdo->prepare('SELECT id, tender_id, login, field, value, created_at FROM portal_changes WHERE id > ? ORDER BY id LIMIT 1000');
    $st->execute([(int)($req['after'] ?? 0)]);
    reply(['changes' => $st->fetchAll()]);
}

if ($action !== 'push') {
    reply(['error' => 'неизвестное действие'], 400);
}

$pdo->beginTransaction();
try {
    $removed = 0;
    if (isset($req['all_ids']) && is_array($req['all_ids'])) {
        $keep = array_flip(array_map('intval', $req['all_ids']));
        $existing = $pdo->query('SELECT id FROM portal_tenders')->fetchAll(PDO::FETCH_COLUMN);
        $drop = array_values(array_filter(array_map('intval', $existing), fn($id) => !isset($keep[$id])));
        foreach (array_chunk($drop, 500) as $chunk) {
            $in = implode(',', $chunk);
            $pdo->exec("DELETE FROM portal_tenders WHERE id IN ($in)");
            $pdo->exec("DELETE FROM portal_views WHERE tender_id IN ($in)");
            $removed += count($chunk);
        }
    }
    $saved = 0;
    $st = $pdo->prepare(
        'REPLACE INTO portal_tenders (id, data, search, relevance, effective, deadline, published, first_seen, nmck,
            user_status, notes, verdict, status_at, notes_at, verdict_at, updated_by)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
    );
    foreach ($req['tenders'] ?? [] as $t) {
        $d = $t['data'] ?? [];
        $search = mb_strtolower(implode(' ', [
            $d['title'] ?? '', $d['customer_name'] ?? '', $d['eis_number'] ?? '', $d['region'] ?? '', $d['customer_inn'] ?? '',
        ]));
        $st->execute([
            (int)$t['id'], json_encode($d, JSON_UNESCAPED_UNICODE), $search, (string)$t['relevance'], (string)$t['effective'],
            $t['deadline'] ?? null, $t['published'] ?? null, $t['first_seen'] ?? null, $t['nmck'] ?? null,
            (string)($t['user_status'] ?? 'новый'), (string)($t['notes'] ?? ''), $t['verdict'] ?? null,
            $t['status_at'] ?? null, $t['notes_at'] ?? null, $t['verdict_at'] ?? null, $t['updated_by'] ?? null,
        ]);
        $saved++;
    }
    if (isset($req['managers']) && is_array($req['managers'])) {
        $pdo->exec('DELETE FROM portal_managers');
        $st = $pdo->prepare('INSERT INTO portal_managers (login, name, password_hash, active) VALUES (?, ?, ?, ?)');
        foreach ($req['managers'] as $m) {
            $st->execute([(string)$m['login'], (string)($m['name'] ?? ''), (string)$m['password_hash'], empty($m['active']) ? 0 : 1]);
        }
    }
    if (isset($req['notifications']) && is_array($req['notifications'])) {
        $pdo->exec('DELETE FROM portal_notifications');
        $st = $pdo->prepare('INSERT INTO portal_notifications (id, title, body, created_at) VALUES (?, ?, ?, ?)');
        foreach ($req['notifications'] as $n) {
            $st->execute([(int)$n['id'], (string)$n['title'], (string)($n['body'] ?? ''), (string)$n['created_at']]);
        }
    }
    if (isset($req['ack'])) {
        $pdo->prepare('DELETE FROM portal_changes WHERE id <= ?')->execute([(int)$req['ack']]);
    }
    if (!empty($req['final'])) {
        meta_set('last_push_at', now_iso());
    }
    $pdo->commit();
} catch (Throwable $e) {
    $pdo->rollBack();
    reply(['error' => 'ошибка записи: ' . $e->getMessage()], 500);
}
reply(['ok' => true, 'saved' => $saved, 'removed' => $removed]);
