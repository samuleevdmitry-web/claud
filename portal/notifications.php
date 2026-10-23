<?php
require __DIR__ . '/lib.php';
$manager = require_manager();
$st = db()->prepare('SELECT notifications_seen_at FROM portal_manager_state WHERE login = ?');
$st->execute([$manager['login']]);
$seen = $st->fetchColumn() ?: '';
$items = db()->query('SELECT * FROM portal_notifications ORDER BY created_at DESC, id DESC LIMIT 100')->fetchAll();
page_header('Уведомления', $manager);
db()->prepare('REPLACE INTO portal_manager_state (login, notifications_seen_at) VALUES (?, ?)')
    ->execute([$manager['login'], now_iso()]);
?>
<h1>Уведомления</h1>
<p class="muted">Сообщения о новых тендерах после каждого прогона. Все новые — во вкладке <a href="index.php?tab=new">«Новые»</a>.</p>
<section class="card">
  <?php foreach ($items as $n): ?>
  <div class="notification level-info <?= $n['created_at'] > $seen ? 'unread' : '' ?>">
    <div class="small muted"><?= h(fmt_dt($n['created_at'])) ?></div>
    <div><b><?= h($n['title']) ?></b> · <a href="index.php?tab=new">открыть новые</a></div>
    <?php if ($n['body']): ?><div class="small pre"><?= h($n['body']) ?></div><?php endif ?>
  </div>
  <?php endforeach ?>
  <?php if (!$items): ?><p class="muted">Уведомлений нет.</p><?php endif ?>
</section>
<?php page_footer();
