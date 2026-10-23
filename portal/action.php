<?php
// Отметки менеджера: «Мой статус», заметки, «Подходит / не подходит», «Просмотрен».
require __DIR__ . '/lib.php';
$manager = require_manager();
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    header('Location: index.php');
    exit;
}
check_csrf();
$id = (int)($_POST['tender_id'] ?? 0);
$field = (string)($_POST['field'] ?? '');
$value = isset($_POST['value']) ? (string)$_POST['value'] : null;
if ($field === 'seen') {
    mark_viewed($manager['login'], $id);
} else {
    if ($field === 'verdict' && $value === '') {
        $value = null; // повторное нажатие снимает оценку
    }
    record_change($manager, $id, $field, $value);
    mark_viewed($manager['login'], $id);
}
header('Location: ' . safe_back((string)($_POST['back'] ?? 'index.php')));
