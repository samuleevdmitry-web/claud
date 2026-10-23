<?php
require __DIR__ . '/lib.php';
start_session();
$next = safe_back(ltrim((string)($_GET['next'] ?? $_POST['next'] ?? 'index.php'), '/'));
if (current_manager()) {
    header('Location: ' . $next);
    exit;
}
$error = '';
$login = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    $login = (string)($_POST['login'] ?? '');
    if (login_blocked()) {
        http_response_code(429);
        $error = 'Слишком много неудачных попыток. Подождите 15 минут.';
    } elseif (try_login($login, (string)($_POST['password'] ?? '')) === null) {
        sleep(1);
        http_response_code(401);
        $error = 'Неверный логин или пароль';
    } else {
        header('Location: ' . $next);
        exit;
    }
}
page_header('Вход', null);
?>
<section class="card login-card">
  <h1>Вход</h1>
  <?php if ($error): ?><div class="flash flash-error"><?= h($error) ?></div><?php endif ?>
  <form method="post" action="login.php" class="try-form">
    <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
    <input type="hidden" name="next" value="<?= h($next) ?>">
    <label>Логин <input name="login" value="<?= h($login) ?>" autocomplete="username" required autofocus></label>
    <label>Пароль <input type="password" name="password" autocomplete="current-password" required></label>
    <div><button type="submit">Войти</button></div>
  </form>
</section>
<?php page_footer();
