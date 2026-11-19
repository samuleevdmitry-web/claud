# Обновление дашборда до последней версии из GitHub — вместо скачивания ZIP.
#   Вручную (в папке проекта):  powershell -ExecutionPolicy Bypass -File .\update.ps1
#   По расписанию (без окон):    его запускает задача из install-autoupdate.ps1 с ключом -Quiet
# Пересобирает дашборд, только если запущенная версия отличается от последней в GitHub. Итоги — в update.log.
param([switch]$Quiet)

Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "update.log"
$deployedFile = Join-Path $PSScriptRoot "update.deployed"  # какая версия сейчас запущена в Docker

function Say([string]$text) {
    Add-Content -Path $log -Value "$(Get-Date -Format 'dd.MM.yyyy HH:mm')  $text" -Encoding UTF8
    if (-not $Quiet) { Write-Host $text }
}

# Не называть функцию «Git»: имена команд в PowerShell без учёта регистра, она перекрыла бы сам git.
function Invoke-Git {
    $out = & git @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' '): $($out -join ' ')" }
    return (($out | ForEach-Object { "$_" }) -join "`n").Trim()
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git не установлен. Установите: winget install --id Git.Git -e — и откройте PowerShell заново."
    }
    if (-not (Test-Path (Join-Path $PSScriptRoot ".git"))) {
        throw "Папка ещё не подключена к GitHub. Один раз выполните команды из README, раздел «Обновление без ZIP»."
    }
    $branch = Invoke-Git rev-parse --abbrev-ref HEAD
    Invoke-Git fetch --quiet origin $branch | Out-Null
    if ((Invoke-Git rev-parse HEAD) -ne (Invoke-Git rev-parse "origin/$branch")) {
        # --autostash: если вы правили файлы проекта (например, wordstat.toml), правки сохранятся
        Invoke-Git merge --ff-only --autostash --quiet "origin/$branch" | Out-Null
        Say "Получена версия $((Invoke-Git rev-parse --short HEAD)): $(Invoke-Git log -1 --format=%s)"
    }
    $head = Invoke-Git rev-parse HEAD
    $deployed = if (Test-Path $deployedFile) { (Get-Content $deployedFile -Raw).Trim() } else { "" }
    if ($head -eq $deployed) {
        if (-not $Quiet) { Say "Обновлений нет: запущена последняя версия $($head.Substring(0, 7))." }
        exit 0
    }
    & docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw "docker compose up не прошёл — запущен ли Docker Desktop? Попробую снова в следующий раз." }
    Set-Content -Path $deployedFile -Value $head -Encoding ASCII
    Say "Дашборд перезапущен на версии $($head.Substring(0, 7)): http://localhost:8080"
}
catch {
    Say "Ошибка обновления: $_"
    if (-not $Quiet) { Read-Host "Нажмите Enter, чтобы закрыть" }
    exit 1
}
