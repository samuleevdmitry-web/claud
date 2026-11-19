# Автообновление дашборда: задача Планировщика Windows раз в 30 минут запускает update.ps1 -Quiet.
# Как только в GitHub появляется новая версия, дашборд пересобирается сам; без изменений ничего не происходит.
#   Включить:  powershell -ExecutionPolicy Bypass -File .\install-autoupdate.ps1
#   Выключить: powershell -ExecutionPolicy Bypass -File .\install-autoupdate.ps1 -Remove
# Работает, пока вы вошли в Windows и запущен Docker Desktop. Итоги запусков — в update.log.
param([switch]$Remove)

$name = "Oldos dashboard update"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Автообновление выключено."
    exit 0
}

$script = Join-Path $PSScriptRoot "update.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $PSScriptRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Quiet"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force `
    -Description "Обновляет дашборд из GitHub (update.ps1 -Quiet) раз в 30 минут" | Out-Null
Write-Host "Автообновление включено: проверка раз в 30 минут. Итоги — в update.log. Выключить: -Remove."
