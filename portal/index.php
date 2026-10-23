<?php
require __DIR__ . '/lib.php';
$manager = require_manager();
$login = $manager['login'];

$tabs = ['new' => 'Новые', 'relevant' => 'Релевантные', 'review' => 'На проверку', 'rejected' => 'Отклонённые', 'all' => 'Все'];
$sorts = ['deadline' => 'по сроку подачи', 'published' => 'по дате публикации', 'nmck' => 'по НМЦК'];
$tab = isset($tabs[$_GET['tab'] ?? '']) ? $_GET['tab'] : (new_count($login) ? 'new' : 'relevant');
$q = trim((string)($_GET['q'] ?? ''));
$status = in_array($_GET['status'] ?? '', STATUSES, true) ? $_GET['status'] : '';
$sort = isset($sorts[$_GET['sort'] ?? '']) ? $_GET['sort'] : 'deadline';
$page = max(1, (int)($_GET['page'] ?? 1));

$where = [];
$args = [];
if ($tab === 'new') {
    $where[] = "t.effective IN ('relevant', 'review') AND t.first_seen >= ?
        AND NOT EXISTS (SELECT 1 FROM portal_views v WHERE v.login = ? AND v.tender_id = t.id)";
    array_push($args, gmdate('Y-m-d\TH:i:s\Z', time() - 14 * 86400), $login);
} elseif ($tab !== 'all') {
    $where[] = 't.effective = ?';
    $args[] = $tab;
}
if ($q !== '') {
    $where[] = 't.search LIKE ?';
    $args[] = '%' . mb_strtolower($q) . '%';
}
if ($status !== '') {
    $where[] = 't.user_status = ?';
    $args[] = $status;
}
$sql = 'FROM portal_tenders t' . ($where ? ' WHERE ' . implode(' AND ', $where) : '');
$st = db()->prepare("SELECT COUNT(*) $sql");
$st->execute($args);
$total = (int)$st->fetchColumn();
$pages = max(1, (int)ceil($total / PER_PAGE));
$nowIso = now_iso();
$order = [
    // Открытые — по ближайшему сроку, без срока — следом, истёкшие — в конце.
    'deadline' => "CASE WHEN t.deadline IS NULL THEN 1 WHEN t.deadline >= '$nowIso' THEN 0 ELSE 2 END, t.deadline",
    'published' => 't.published DESC',
    'nmck' => 't.nmck DESC',
][$sort];
$st = db()->prepare("SELECT t.* $sql ORDER BY $order, t.id DESC LIMIT " . PER_PAGE . ' OFFSET ' . (($page - 1) * PER_PAGE));
$st->execute($args);
$rows = $st->fetchAll();

$viewed = [];
if ($rows) {
    $ids = implode(',', array_map(fn($r) => (int)$r['id'], $rows));
    $st = db()->prepare("SELECT tender_id FROM portal_views WHERE login = ? AND tender_id IN ($ids)");
    $st->execute([$login]);
    $viewed = array_flip($st->fetchAll(PDO::FETCH_COLUMN));
}
$counts = [];
foreach (db()->query('SELECT effective, COUNT(*) c FROM portal_tenders GROUP BY effective') as $r) {
    $counts[$r['effective']] = (int)$r['c'];
}
$counts['all'] = array_sum($counts);
$counts['new'] = new_count($login);

function qs(array $override): string
{
    global $tab, $q, $status, $sort;
    $p = array_merge(['tab' => $tab, 'q' => $q, 'status' => $status, 'sort' => $sort], $override);
    return http_build_query(array_filter($p, fn($v) => $v !== '' && $v !== null && $v !== 1));
}

$back = 'index.php?' . qs(['page' => $page]);
page_header($tabs[$tab], $manager);
?>
<div class="tabs">
  <?php foreach ($tabs as $key => $label): ?>
    <a href="index.php?<?= h(qs(['tab' => $key, 'page' => 1])) ?>" class="tab <?= $tab === $key ? 'active' : '' ?>"><?= h($label) ?>
      <span class="count"><?= (int)($counts[$key] ?? 0) ?></span></a>
  <?php endforeach ?>
</div>

<form class="filters card" method="get" action="index.php">
  <input type="hidden" name="tab" value="<?= h($tab) ?>">
  <label>Поиск <input name="q" value="<?= h($q) ?>" placeholder="название, заказчик, номер"></label>
  <label>Мой статус <select name="status"><option value="">все</option>
    <?php foreach (STATUSES as $s): ?><option <?= $status === $s ? 'selected' : '' ?>><?= h($s) ?></option><?php endforeach ?>
  </select></label>
  <label>Сортировка <select name="sort">
    <?php foreach ($sorts as $k => $v): ?><option value="<?= h($k) ?>" <?= $sort === $k ? 'selected' : '' ?>><?= h($v) ?></option><?php endforeach ?>
  </select></label>
  <div class="filter-actions"><button type="submit">Показать</button> <a href="index.php?tab=<?= h($tab) ?>">Сбросить фильтры</a></div>
</form>

<div class="list-head"><span class="muted">Найдено: <?= $total ?></span></div>
<?php if ($rows): ?>
<div class="card table-card"><table class="tenders">
  <tr><th>Срок подачи</th><th>Статус</th><th>Закупка</th><th>Заказчик / регион</th><th>НМЦК</th><th>Площадки</th><th>Подходит?</th></tr>
  <?php foreach ($rows as $t):
      $d = json_decode($t['data'], true) ?: [];
      $deadlineTs = $t['deadline'] ? strtotime($t['deadline']) : null;
      $urgent = $deadlineTs && $deadlineTs >= time() && $deadlineTs < time() + 3 * 86400;
      $expired = $deadlineTs && $deadlineTs < time(); ?>
  <tr class="<?= $urgent ? 'urgent' : ($expired ? 'expired' : '') ?>">
    <td class="nowrap"><?= h(fmt_dt($t['deadline'])) ?><?php if ($urgent): ?><div class="small warn">меньше 3 дней</div><?php endif ?></td>
    <td><span class="pill pill-<?= h($t['effective']) ?>"><?= h(RELEVANCE_LABELS[$t['effective']] ?? $t['effective']) ?></span>
      <?php if (!isset($viewed[$t['id']]) && $t['first_seen'] >= gmdate('Y-m-d\TH:i:s\Z', time() - 14 * 86400)): ?><span class="pill pill-new">новый</span><?php endif ?></td>
    <td><a href="tender.php?id=<?= (int)$t['id'] ?>"><?= h($d['title'] ?? '') ?></a>
      <div class="small muted"><?= h($d['law'] ?? '') ?><?= !empty($d['eis_number']) ? ' · № ' . h($d['eis_number']) : '' ?><?= !empty($d['categories']) ? ' · ' . h(implode(', ', $d['categories'])) : '' ?></div>
      <?php if ($t['user_status'] !== 'новый'): ?><div class="small"><b><?= h($t['user_status']) ?></b></div><?php endif ?></td>
    <td><?= h($d['customer_name'] ?? '') ?><div class="small muted"><?= h($d['region'] ?? '') ?></div></td>
    <td class="nowrap"><?= h(fmt_money($t['nmck'])) ?></td>
    <td class="small"><?php foreach ($d['sources'] ?? [] as $s): ?><a href="<?= h($s['url']) ?>" target="_blank" rel="noopener"><?= h($s['title']) ?></a><br><?php endforeach ?></td>
    <td><?= verdict_buttons($t, $back) ?></td>
  </tr>
  <?php endforeach ?>
</table></div>
<?php if ($pages > 1): ?>
<div class="pager">
  <?php if ($page > 1): ?><a href="index.php?<?= h(qs(['page' => $page - 1])) ?>">← назад</a><?php endif ?>
  <span>стр. <?= $page ?> из <?= $pages ?></span>
  <?php if ($page < $pages): ?><a href="index.php?<?= h(qs(['page' => $page + 1])) ?>">вперёд →</a><?php endif ?>
</div>
<?php endif ?>
<?php else: ?>
  <div class="card muted">Тендеров нет.<?= $tab === 'new' ? ' Новые появятся после следующего прогона.' : '' ?></div>
<?php endif ?>
<?php page_footer();
