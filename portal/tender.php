<?php
require __DIR__ . '/lib.php';
$manager = require_manager();
$t = tender((int)($_GET['id'] ?? 0));
if ($t === null) {
    http_response_code(404);
    page_header('Не найден', $manager);
    echo '<p><a href="index.php">← к списку</a></p><div class="card">Тендер не найден или уже не актуален.</div>';
    page_footer();
    exit;
}
mark_viewed($manager['login'], (int)$t['id']);
$d = $t['d'];
$back = 'tender.php?id=' . (int)$t['id'];
$csrf = h(csrf_token());
page_header(mb_substr($d['title'] ?? '', 0, 60), $manager);
?>
<p><a href="index.php">← к списку</a></p>
<h1 class="tender-title"><?= h($d['title'] ?? '') ?></h1>
<p><b>Подходит?</b> <?= verdict_buttons($t, $back) ?>
  <span class="pill pill-<?= h($t['effective']) ?>"><?= h(RELEVANCE_LABELS[$t['effective']] ?? '') ?></span>
  <?php if ($t['updated_by']): ?><span class="small muted">последняя отметка: <?= h($t['updated_by']) ?></span><?php endif ?></p>

<div class="grid2">
<section class="card">
  <h2>Закупка</h2>
  <table class="kv">
    <tr><th>Срок подачи</th><td><b><?= h(fmt_dt($t['deadline'])) ?></b></td></tr>
    <tr><th>Заказчик</th><td><?= h($d['customer_name'] ?? '—') ?><?= !empty($d['customer_inn']) ? ' <span class="muted">ИНН ' . h($d['customer_inn']) . '</span>' : '' ?></td></tr>
    <tr><th>Регион</th><td><?= h($d['region'] ?? '—') ?></td></tr>
    <tr><th>Место поставки</th><td><?= h(($d['delivery_place'] ?? '') ?: '—') ?></td></tr>
    <tr><th>Закон</th><td><?= h($d['law'] ?? '') ?></td></tr>
    <tr><th>Способ</th><td><?= h(($d['procedure_type'] ?? '') ?: '—') ?></td></tr>
    <tr><th>НМЦК</th><td><?= h(fmt_money($t['nmck'])) ?></td></tr>
    <tr><th>Опубликовано</th><td><?= h(fmt_dt($t['published'])) ?></td></tr>
    <tr><th>Статус на площадке</th><td><?= h(($d['status'] ?? '') ?: '—') ?></td></tr>
    <tr><th>Номер ЕИС</th><td><?= h(($d['eis_number'] ?? '') ?: '—') ?></td></tr>
    <tr><th>Коды ОКПД2/КТРУ</th><td><?= h(implode(', ', $d['okpd2_codes'] ?? []) ?: '—') ?></td></tr>
  </table>
  <h3>Площадки</h3>
  <ul><?php foreach ($d['sources'] ?? [] as $s): ?><li><a href="<?= h($s['url']) ?>" target="_blank" rel="noopener"><?= h($s['title']) ?></a></li><?php endforeach ?></ul>
</section>

<section class="card">
  <h2>Работа с тендером</h2>
  <form method="post" action="action.php" class="inline">
    <input type="hidden" name="csrf" value="<?= $csrf ?>"><input type="hidden" name="tender_id" value="<?= (int)$t['id'] ?>">
    <input type="hidden" name="field" value="user_status"><input type="hidden" name="back" value="<?= h($back) ?>">
    <label>Мой статус <select name="value" onchange="this.form.submit()">
      <?php foreach (STATUSES as $s): ?><option <?= $t['user_status'] === $s ? 'selected' : '' ?>><?= h($s) ?></option><?php endforeach ?>
    </select></label> <noscript><button type="submit">Сохранить</button></noscript>
  </form>
  <form method="post" action="action.php" class="try-form">
    <input type="hidden" name="csrf" value="<?= $csrf ?>"><input type="hidden" name="tender_id" value="<?= (int)$t['id'] ?>">
    <input type="hidden" name="field" value="notes"><input type="hidden" name="back" value="<?= h($back) ?>">
    <label>Заметки <textarea name="value" rows="5"><?= h($t['notes']) ?></textarea></label>
    <div><button type="submit">Сохранить заметки</button></div>
  </form>
  <?php if (!empty($d['reasons'])): ?>
  <h3>Почему такой статус</h3>
  <ul class="reasons"><?php foreach ($d['reasons'] as $r): ?><li><?= h($r) ?></li><?php endforeach ?></ul>
  <?php endif ?>
  <?php if (!empty($d['categories'])): ?><p>Категории: <?= h(implode(', ', $d['categories'])) ?></p><?php endif ?>
</section>
</div>

<?php if (!empty($d['positions'])): ?>
<section class="card">
  <h2>Позиции (<?= count($d['positions']) ?>)</h2>
  <table>
    <tr><th>№</th><th>Наименование</th><th>Кол-во</th><th>Ед.</th><th>ОКПД2/КТРУ</th><th>Цена</th></tr>
    <?php foreach ($d['positions'] as $i => $p): ?>
    <tr><td><?= $i + 1 ?></td><td><?= h($p['name'] ?? '') ?></td><td><?= h($p['qty'] ?? '') ?></td><td><?= h($p['unit'] ?? '') ?></td>
      <td><code><?= h($p['code'] ?? '') ?></code></td><td class="nowrap"><?= isset($p['price']) ? h(fmt_money($p['price'])) : '' ?></td></tr>
    <?php endforeach ?>
  </table>
</section>
<?php endif ?>
<?php page_footer();
