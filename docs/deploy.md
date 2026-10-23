# Размещение на сервере (доступ менеджеров через интернет)

После размещения приложение работает круглосуточно: прогоны идут по расписанию без вашего
компьютера, а администратор и менеджеры входят по адресу вида `https://tenders.velmorium.ru`
под своими логинами.

## 1. Что нужно

- **VPS (виртуальный сервер) в российском дата-центре.** Не «виртуальный хостинг для сайтов»:
  на нём нельзя запустить Docker и фоновые прогоны. Подойдёт любой VPS с Ubuntu 22.04 или
  24.04: 2 ГБ памяти, 1–2 ядра, 20–30 ГБ диска. Российский IP обязателен: ЕИС и часть площадок
  не пускают зарубежные адреса.
- **Домен или поддомен**, например `tenders.velmorium.ru`. Там, где управляется домен
  `velmorium.ru` (регистратор или DNS-хостинг), добавьте запись **A**: имя `tenders`, значение —
  IP сервера.

## 2. Подготовка сервера

Подключитесь к серверу (Windows: PowerShell или Терминал):

```bash
ssh root@IP_СЕРВЕРА
```

Установите Docker (если у провайдера нет готового образа с Docker) и откройте только нужные порты:

```bash
curl -fsSL https://get.docker.com | sh
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```

Порт 8000 открывать не нужно: приложение слушает его только внутри сервера, наружу смотрит
HTTPS-прокси Caddy.

## 3. Код и настройки

Загрузите папку проекта на сервер, например в `/root/tenders` (через WinSCP или
`scp -r папка root@IP_СЕРВЕРА:/root/tenders`), и создайте `.env` из образца:

```bash
cd /root/tenders
cp .env.example .env
nano .env
```

В `.env` обязательно поменяйте:

```ini
APP_USERNAME=admin                     # логин администратора
APP_PASSWORD=длинный-надёжный-пароль   # не «change-me»: сайт будет в интернете
POSTGRES_PASSWORD=другой-длинный-пароль  # задать ДО первого запуска
DOMAIN=tenders.velmorium.ru
APP_BASE_URL=https://tenders.velmorium.ru
FORWARDED_ALLOW_IPS=*                  # доверять заголовкам прокси Caddy (порт 8000 закрыт снаружи)
```

Там же — ключ ГосПлана, токен импорта и почта для писем менеджерам (`SMTP_*`), как в README.

## 4. Запуск

```bash
docker compose --profile https up -d --build
```

Через 1–2 минуты откройте `https://tenders.velmorium.ru`: сертификат Caddy получает сам.
Если сайт не открывается — проверьте, что запись A указывает на IP сервера
(`ping tenders.velmorium.ru`), и посмотрите журнал: `docker compose logs caddy app`.

Войдите администратором и заведите менеджеров на странице «Пользователи».

## 5. Перенос данных с компьютера (если нужно сохранить найденное)

Найденные тендеры, оценки «подходит / не подходит» и правки ключей лежат в базе. Чтобы перенести их:

На компьютере (в папке проекта, PowerShell или терминал):

```bash
docker compose exec db pg_dump -U tenders -Fc -f /tmp/tenders.dump tenders
docker compose cp db:/tmp/tenders.dump tenders.dump
```

Скопируйте `tenders.dump` на сервер в `/root/tenders`, затем на сервере:

```bash
docker compose cp tenders.dump db:/tmp/tenders.dump
docker compose exec db pg_restore -U tenders -d tenders --clean --if-exists /tmp/tenders.dump
docker compose --profile https restart app
```

После переноса остановите приложение на компьютере (`docker compose down`), чтобы площадки не
обходились дважды.

## 6. Проверка площадок с сервера

Адрес дата-центра площадки могут воспринимать иначе, чем домашний. Запустите на сервере разведку
и пришлите архив разработчику:

```bash
docker compose exec app python -m app.cli probe
ls data/probe/
```

Архив — в `/root/tenders/data/probe/`, скачайте его через WinSCP или `scp`.

## 7. Обновление и резервные копии

Обновление: загрузите новые файлы проекта поверх старых (`.env` и `data/` не трогайте) и выполните

```bash
docker compose --profile https up -d --build
```

Ежедневная копия базы (хранится 14 дней) — добавьте в `crontab -e`:

```cron
0 3 * * * mkdir -p /root/backup && cd /root/tenders && docker compose exec -T db pg_dump -U tenders -Fc tenders > /root/backup/tenders_$(date +\%F).dump && find /root/backup -name '*.dump' -mtime +14 -delete
```

## 8. Импорт через Claude in Chrome

Страница импорта теперь — `https://tenders.velmorium.ru/import` (вход администратором). В
промпте из `docs/claude_in_chrome.md` замените `http://127.0.0.1:8000` на этот адрес.

## Безопасность

- Вход только по HTTPS; после 10 неверных паролей с одного адреса вход блокируется на 15 минут.
- У каждого менеджера свой логин; смена пароля или снятие галочки «доступ открыт» сразу
  закрывает ему вход. Пароль администратора никому не передавайте.
- База данных наружу не открыта, служебные страницы API отключены.
