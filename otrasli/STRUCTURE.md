# Страница «Отрасли» — структура и обоснование

Прототип: [`index.html`](index.html). Файл сгенерирован из JSON-блока `#data` в `index.html` — правки вносить туда.

## Откуда данные

- **Направления и решения** — ровно меню «Услуги» на cns-corp.ru: 7 направлений, 30 решений + «Расширенная гарантия» из подвала (отнесена к ИТ-поддержке). Страницы `/decision/` не используются: их нет в меню, они закрыты в robots.txt.
- **Отрасли** — 8 штук, адреса — уже заведённые на сайте `/industries/<slug>/` (взяты из блока «Отраслевые решения» на странице «Услуги ИБ»).
- **Связь «отрасль → решение»** ставится только с основанием:
  - **сайт** — страница решения сама называет отрасль или объект (дословная цитата; блоки «Отраслевые решения» на страницах ИБ и AutoID, разделы «Кому нужна услуга», «Сферы применения»);
  - **кейс** — решение применено в проекте из «Проектов» у клиента этой отрасли;
  - **рекомендация** — логичная связь, которой на сайте нет. Показана отдельно (жёлтым), её можно скрыть переключателем.
- Решения без отраслевых оснований (аудит, серверы, облако, поддержка и т. п.) не размазаны по всем отраслям, а вынесены в блок «Для любой отрасли».
- Автопроверка: 64 цитаты найдены дословно на своих страницах; набор решений совпадает с меню; каждый кейс-основание действительно использует решение; числа в описаниях кейсов есть в текстах кейсов; все ссылки отвечают 200, кроме `/industries/logistics/` (страницы нет).

## Сводка

| Отрасль | Страница | Направлений | Решений (подтв.) | Рекомендаций | Кейсов |
|---|---|---|---|---|---|
| Ритейл и FMCG | `/industries/retail-and-fmcg/` | 6 | 14 | 0 | 4 |
| Агропромышленный комплекс | `/industries/agropromyshlennyj-kompleks/` | 6 | 4 | 5 | 1 |
| Производство | `/industries/proizvodstvo/` | 6 | 14 | 2 | 5 |
| Логистика | `/industries/logistics/` — **нет на сайте (404)** | 6 | 14 | 0 | 3 |
| Строительство | `/industries/stroitelstvo/` | 5 | 2 | 5 | 1 |
| Образование | `/industries/obrazovanie/` | 5 | 11 | 2 | 2 |
| Медицина | `/industries/medicina/` | 6 | 10 | 0 | 5 |
| Социальная сфера | `/industries/socialnaya-sfera/` | 5 | 9 | 2 | 0 |

## 1. Ритейл и FMCG

Оснащаем магазины под ключ по единому стандарту, наводим порядок в учёте и маркировке, ставим экраны в торговом зале и поддерживаем всю сеть точек.

**[Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)**

- [AutoID: учёт и маркировка](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/) — Приёмка и пересчёт товара, учёт остатков, маркировка, работа с возвратами и пересортицей. _(сайт)_
  - [AutoID](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/): «Приёмка и пересчёт товара, учёт остатков, маркировка, работа с возвратами и пересортицей.»
- [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) — Контроль привилегированного доступа (PAM), DLP, мониторинг инцидентов SIEM/SOAR, защита e-com и веб-ресурсов. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «Контроль привилегированного доступа (PAM), Предотвращение утечек данных (DLP), Мониторинг инцидентов (SIEM/SOAR), Защита e-com и веб-ресурсов»
- [Серверные решения](https://cns-corp.ru/it-uslugi/infrastruktura/servernye-resheniya/) — Серверы для магазинов и растущей сети без простоев. _(кейс)_
  - кейс [Комплексное оснащение сети фешн-магазинов ООО «ПикНик»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-seti-feshn-magazinov-ooo-piknik/)
  - кейс [Поставка и настройка серверного оборудования Supermicro и Mikrotik](https://cns-corp.ru/kejsy/postavka-i-nastrojka-servernogo-oborudovaniya-supermicro-i-mikrotik/)

**[Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)**

- [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/) — Контроль доступа в магазинах, торговых и развлекательных центрах, ресторанах. _(сайт)_
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/): «торговым и развлекательным центрам, ресторанам»
  - кейс [Комплексное оснащение сети фешн-магазинов ООО «ПикНик»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-seti-feshn-magazinov-ooo-piknik/)
- [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/) — Видеонаблюдение в торговом зале и подсобных помещениях. _(кейс)_
  - кейс [Комплексное оснащение сети фешн-магазинов ООО «ПикНик»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-seti-feshn-magazinov-ooo-piknik/)

**[ИТ-поддержка](https://cns-corp.ru/it-uslugi/it-support/)**

- [IT-аутсорсинг](https://cns-corp.ru/it-uslugi/it-support/it-autsorsing/) — Поддержка сети точек: удалённая линия, выезды, открытие и закрытие объектов. _(кейс)_
  - кейс [IT-аутсорсинг. Техническая поддержка](https://cns-corp.ru/kejsy/it-autsorsing-tehnicheskaya-podderzhka/)
  - кейс [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/)

**[Сети](https://cns-corp.ru/it-uslugi/seti/)**

- [Сетевые решения](https://cns-corp.ru/it-uslugi/seti/setevye-resheniya/) — Сеть магазина и торгового центра под ключ. _(сайт)_
  - [Сетевые решения](https://cns-corp.ru/it-uslugi/seti/setevye-resheniya/): «сетевой инфраструктуры торгового центра»
  - кейс [Комплексное оснащение сети фешн-магазинов ООО «ПикНик»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-seti-feshn-magazinov-ooo-piknik/)
  - кейс [Поставка и настройка серверного оборудования Supermicro и Mikrotik](https://cns-corp.ru/kejsy/postavka-i-nastrojka-servernogo-oborudovaniya-supermicro-i-mikrotik/)
- [Монтаж СКС](https://cns-corp.ru/it-uslugi/seti/montazh-sks/) — Кабельная система для новых точек, касс, экранов и инфокиосков. _(кейс)_
  - кейс [Комплексное оснащение сети фешн-магазинов ООО «ПикНик»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-seti-feshn-magazinov-ooo-piknik/)
  - кейс [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/)
- [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/) — Стабильный Wi-Fi в торговых пространствах и на складах. _(сайт)_
  - [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/): «(офисы, склады, торговые пространства)»
- [Радиообследование Wi-Fi](https://cns-corp.ru/it-uslugi/seti/radioobsledovanie-wi-fi/) — Обследование торговой площади перед развёртыванием Wi-Fi. _(сайт)_
  - [Радиообследование Wi-Fi](https://cns-corp.ru/it-uslugi/seti/radioobsledovanie-wi-fi/): «(офис, склад, торговая площадь)»

**[Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/)**

- [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/) — Динамическое оформление витрин, рекламные ролики, цифровые расписания акций. _(сайт)_
  - [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/): «Розничным сетям, магазинам и торговым центрам для динамического оформления витрин»
  - кейс [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/)
- [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/) — Цифровые витрины, интерактивные киоски, динамическое отображение цен и меню. _(сайт)_
  - [Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/): «Розничный бизнес для внедрения цифровых витрин, интерактивных киосков и динамических систем отображения цен и меню»
- [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/) — Видеостены для торговых и выставочных центров. _(сайт)_
  - [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/): «Объектов торговли, развлечений и общественных пространств (торговые и выставочные центры»

**[Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)**

- [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/) — ВКС для связи офиса с сетью и работы с персоналом. _(сайт)_
  - [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/): «государственный и промышленный сектор, техническая поддержка, работа с кадрами, торговля»

**Кейсы:** 

- [Комплексное оснащение сети фешн-магазинов ООО «ПикНик»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-seti-feshn-magazinov-ooo-piknik/) — Три магазина в премиальных ТЦ по единому стандарту: серверы, сеть, СКС, кассы, видеонаблюдение и СКУД — открытие в срок.
- [IT-аутсорсинг. Техническая поддержка](https://cns-corp.ru/kejsy/it-autsorsing-tehnicheskaya-podderzhka/) — Сеть АЗС с продуктовыми магазинами и кафе, более 100 объектов: центр удалённой поддержки, база знаний, выездные специалисты.
- [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/) — Федеральная сеть аптек (800+ точек): кабельная система под рекламные ТВ и инфокиоски в 500+ аптеках, затем ИТ-обслуживание.
- [Поставка и настройка серверного оборудования Supermicro и Mikrotik](https://cns-corp.ru/kejsy/postavka-i-nastrojka-servernogo-oborudovaniya-supermicro-i-mikrotik/) — Лидер рынка instore-коммуникаций и POSM для розницы: расширение серверной и сетевой инфраструктуры без простоев.

## 2. Агропромышленный комплекс

Защищаем промышленный интернет вещей и периметр, переносим вычисления в облако и берём ИТ удалённых площадок на аутсорсинг.

**[Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)**

- [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) — Защита индустриального интернета вещей (IIoT), аудит и мониторинг уязвимостей, комплексная защита периметра. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «Защита индустриального Интернета вещей (IIoT), аутсорсинг и облачные сервисы, аудит и мониторинг уязвимостей, комплексная защита периметра»
- [AutoID: учёт и маркировка](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/) — Учёт партий и маркировка на переработке и складах. _(**рекомендация**)_
  - нет на сайте — Прямо на сайте АПК не назван. Сценарии AutoID для производства и склада («учёт партий и серий», маркировка «Честный знак») подходят пищевой переработке.

**[Облачные сервисы](https://cns-corp.ru/it-uslugi/oblachnye-servisy/)**

- [Аренда серверных стоек](https://cns-corp.ru/it-uslugi/oblachnye-servisy/arenda-stoek/) — Размещение оборудования в ЦОД вместо серверных на каждой площадке. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «аутсорсинг и облачные сервисы»
- [Виртуальные серверы](https://cns-corp.ru/it-uslugi/oblachnye-servisy/virtualnye/) — Вычислительные ресурсы в облаке для удалённых площадок. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «аутсорсинг и облачные сервисы»

**[Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)**

- [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/) — Охрана территорий, складов и техники. _(**рекомендация**)_
  - нет на сайте — Отраслевой связи на сайте нет; страница видеонаблюдения — про контроль территории и номера транспорта.

**[ИТ-поддержка](https://cns-corp.ru/it-uslugi/it-support/)**

- [IT-аутсорсинг](https://cns-corp.ru/it-uslugi/it-support/it-autsorsing/) — ИТ и ИБ на аутсорсинге для удалённых площадок. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «аутсорсинг и облачные сервисы»

**[Сети](https://cns-corp.ru/it-uslugi/seti/)**

- [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/) — Покрытие для IoT-датчиков и техники. _(**рекомендация**)_
  - нет на сайте — На странице Wi-Fi — «внедрение IoT-устройств», в блоке ИБ для АПК — IIoT; прямой связи с АПК нет.
- [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/) — Связь между удалёнными объектами хозяйства. _(**рекомендация**)_
  - нет на сайте — На странице ВОЛС — «промышленным предприятиям… для связи между удалёнными технологическими узлами»; АПК не назван.

**[Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)**

- [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/) — Связь управляющей компании с удалёнными хозяйствами. _(**рекомендация**)_
  - нет на сайте — ВКС названа для промышленного сектора; АПК отдельно не упомянут.

**Кейсы:** 

- [Годовой контракт на 21 юрлицо: как команда CNS выстроила систему оснащения рабочих мест для крупного рыбопромышленного холдинга](https://cns-corp.ru/kejsy/godovoi-kontrakt-na-21-yurliczo-kak-komanda-cns-vystroila-sistemu-osnashheniya-rabochih-mest-dlya-krupnogo-rybopromyshlennogo-holdinga/) — Ежемесячные поставки рабочих мест для дочерних предприятий холдинга в течение года, 21 отдельный договор.

## 3. Производство

Строим отказоустойчивую инфраструктуру и ЦОД, переводим на отечественный стек, защищаем АСУ ТП и наводим порядок в учёте партий и связи в цехах.

**[Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)**

- [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) — Защита АСУ ТП, PAM, аудит и мониторинг SIEM/SOAR, DLP; отдельный раздел — безопасность КИИ и АСУ ТП. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «Защита АСУ ТП, Контроль привилегированного доступа (PAM), Аудит и мониторинг (SIEM / SOAR), Предотвращение утечек данных (DLP)»
  - кейс [Модернизация КСПД и создание отказоустойчивого ядра сети в распределённом ЦОД](https://cns-corp.ru/kejsy/modernizacziya-kspd-i-sozdanie-otkazoustojchivogo-yadra-seti-v-raspredelyonnom-czod/)
- [AutoID: учёт и маркировка](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/) — Движение сырья, полуфабрикатов и готовой продукции между участками и складами, учёт партий и серий. _(сайт)_
  - [AutoID](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/): «Движение сырья, полуфабрикатов и готовой продукции между участками и складами, учёт партий и серий.»
- [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/) — Отечественные PLM, MES и промышленная идентификация; перевод серверов, СХД и виртуализации на российский стек. _(сайт)_
  - [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/): «Инженерные системы класса PLM»
  - [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/): «PROF-IT MES: Машиностроение»
  - кейс [Импортозамещение серверной инфраструктуры и платформы виртуализации на государственном уровне](https://cns-corp.ru/kejsy/importozameshhenie-servernoj-infrastruktury-i-platformy-virtualizaczii-na-gosudarstvennom-urovne/)
  - кейс [Модернизация систем видеонаблюдения и контроля доступа (СКУД) для лидера нефтегазовой отрасли](https://cns-corp.ru/kejsy/modernizacziya-sistem-videonablyudeniya-i-kontrolya-dostupa-skud-dlya-lidera-neftegazovoi-otrasli/)
- [Серверные решения](https://cns-corp.ru/it-uslugi/infrastruktura/servernye-resheniya/) — Отказоустойчивые серверы и СХД, резервное копирование, новые ЦОД. _(кейс)_
  - кейс [Построение отказоустойчивой ИТ-инфраструктуры хранения и резервного копирования для горнодобывающей компании «Покровский рудник»](https://cns-corp.ru/kejsy/postroenie-otkazoustojchivoj-it-infrastruktury-hraneniya-i-rezervnogo-kopirovaniya-dlya-gornodobyvayushhej-kompanii-pokrovskij-rudnik/)
  - кейс [Развёртывание серверной инфраструктуры и СХД для нового ЦОД легкой промышленности](https://cns-corp.ru/kejsy/razvertyvanie-servernoi-infrastruktury-i-shd-dlya-novogo-czod-legkoi-promyshlennosti/)
  - кейс [Импортозамещение серверной инфраструктуры и платформы виртуализации на государственном уровне](https://cns-corp.ru/kejsy/importozameshhenie-servernoj-infrastruktury-i-platformy-virtualizaczii-na-gosudarstvennom-urovne/)
- [Виртуализация](https://cns-corp.ru/it-uslugi/infrastruktura/virtualizacziya/) — Миграция виртуальной инфраструктуры без остановки сервисов. _(кейс)_
  - кейс [Импортозамещение серверной инфраструктуры и платформы виртуализации на государственном уровне](https://cns-corp.ru/kejsy/importozameshhenie-servernoj-infrastruktury-i-platformy-virtualizaczii-na-gosudarstvennom-urovne/)
- [Аудиты и обследования](https://cns-corp.ru/it-uslugi/infrastruktura/audit/) — Аудит корпоративной сети перед модернизацией. _(кейс)_
  - кейс [Модернизация КСПД и создание отказоустойчивого ядра сети в распределённом ЦОД](https://cns-corp.ru/kejsy/modernizacziya-kspd-i-sozdanie-otkazoustojchivogo-yadra-seti-v-raspredelyonnom-czod/)

**[Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)**

- [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/) — Пропускной режим на промышленных предприятиях и складах. _(сайт)_
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/): «промышленным предприятиям, складам»
  - кейс [Модернизация систем видеонаблюдения и контроля доступа (СКУД) для лидера нефтегазовой отрасли](https://cns-corp.ru/kejsy/modernizacziya-sistem-videonablyudeniya-i-kontrolya-dostupa-skud-dlya-lidera-neftegazovoi-otrasli/)
- [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/) — Контроль производственных помещений и территории. _(сайт)_
  - [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/): «в производственном или офисном помещении»
  - кейс [Модернизация систем видеонаблюдения и контроля доступа (СКУД) для лидера нефтегазовой отрасли](https://cns-corp.ru/kejsy/modernizacziya-sistem-videonablyudeniya-i-kontrolya-dostupa-skud-dlya-lidera-neftegazovoi-otrasli/)

**[ИТ-поддержка](https://cns-corp.ru/it-uslugi/it-support/)**

- [Расширенная гарантия](https://cns-corp.ru/it-uslugi/rasshirennaya-garantiya/) — Поддержка серверов и СХД 24/7 с ЗИП на весь срок эксплуатации. _(кейс)_
  - кейс [Развёртывание серверной инфраструктуры и СХД для нового ЦОД легкой промышленности](https://cns-corp.ru/kejsy/razvertyvanie-servernoi-infrastruktury-i-shd-dlya-novogo-czod-legkoi-promyshlennosti/)

**[Сети](https://cns-corp.ru/it-uslugi/seti/)**

- [Сетевые решения](https://cns-corp.ru/it-uslugi/seti/setevye-resheniya/) — Отказоустойчивое ядро корпоративной сети. _(кейс)_
  - кейс [Модернизация КСПД и создание отказоустойчивого ядра сети в распределённом ЦОД](https://cns-corp.ru/kejsy/modernizacziya-kspd-i-sozdanie-otkazoustojchivogo-yadra-seti-v-raspredelyonnom-czod/)
- [Монтаж СКС](https://cns-corp.ru/it-uslugi/seti/montazh-sks/) — СКС для производственных помещений, серверных и ЦОД. _(сайт)_
  - [Монтаж СКС](https://cns-corp.ru/it-uslugi/seti/montazh-sks/): «(центры обработки данных, серверные, производственные помещения)»
- [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/) — Связь между корпусами и удалёнными технологическими узлами. _(сайт)_
  - [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/): «Промышленным предприятиям и объектам инфраструктуры (транспорт, энергетика)»
- [Радиообследование Wi-Fi](https://cns-corp.ru/it-uslugi/seti/radioobsledovanie-wi-fi/) — Обследование цехов перед развёртыванием Wi-Fi. _(сайт)_
  - [Радиообследование Wi-Fi](https://cns-corp.ru/it-uslugi/seti/radioobsledovanie-wi-fi/): «производственные цеха»
- [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/) — Беспроводная сеть для цехов по итогам радиообследования. _(**рекомендация**)_
  - нет на сайте — Производство названо на странице радиообследования; на странице Wi-Fi — нет.

**[Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/)**

- [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/) — Видеостены для диспетчерских и ситуационных центров. _(**рекомендация**)_
  - нет на сайте — Диспетчерские и ситуационные центры на сайте названы для гос- и оперативных служб и корпоративного сектора, не для производства.

**[Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)**

- [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/) — ВКС для промышленного сектора и связи с площадками. _(сайт)_
  - [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/): «государственный и промышленный сектор»

**Кейсы:** 

- [Модернизация систем видеонаблюдения и контроля доступа (СКУД) для лидера нефтегазовой отрасли](https://cns-corp.ru/kejsy/modernizacziya-sistem-videonablyudeniya-i-kontrolya-dostupa-skud-dlya-lidera-neftegazovoi-otrasli/) — Актуализация проекта видеонаблюдения и СКУД, построенного на импортном оборудовании, поставки которого прекращены.
- [Построение отказоустойчивой ИТ-инфраструктуры хранения и резервного копирования для горнодобывающей компании «Покровский рудник»](https://cns-corp.ru/kejsy/postroenie-otkazoustojchivoj-it-infrastruktury-hraneniya-i-rezervnogo-kopirovaniya-dlya-gornodobyvayushhej-kompanii-pokrovskij-rudnik/) — Хранение, обработка и резервное копирование данных для рудников в условиях ограничений поставок.
- [Развёртывание серверной инфраструктуры и СХД для нового ЦОД легкой промышленности](https://cns-corp.ru/kejsy/razvertyvanie-servernoi-infrastruktury-i-shd-dlya-novogo-czod-legkoi-promyshlennosti/) — 15 серверов Dell и СХД для нового ЦОД и техническая поддержка на три года.
- [Импортозамещение серверной инфраструктуры и платформы виртуализации на государственном уровне](https://cns-corp.ru/kejsy/importozameshhenie-servernoj-infrastruktury-i-platformy-virtualizaczii-na-gosudarstvennom-urovne/) — Крупный государственный заказчик из промышленного сектора: переход на Aquarius, Aerodisk и zVirt без остановки сервисов.
- [Модернизация КСПД и создание отказоустойчивого ядра сети в распределённом ЦОД](https://cns-corp.ru/kejsy/modernizacziya-kspd-i-sozdanie-otkazoustojchivogo-yadra-seti-v-raspredelyonnom-czod/) — Производственно-логистический холдинг: офис, производство, склад, ЦОД и более десяти филиалов. Аудит сети, новое ядро и управление сетевой безопасностью.

## 4. Логистика

Автоматизируем складские операции, обеспечиваем Wi-Fi в стеллажных зонах, контроль территории и транспорта, табло и экраны на объектах.

**[Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)**

- [AutoID: учёт и маркировка](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/) — Приёмка, размещение, отбор, упаковка, отгрузка, кросс-докинг, инвентаризация. _(сайт)_
  - [AutoID](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/): «Приёмка, размещение, отбор, упаковка, отгрузка, кросс-докинг, инвентаризация»
- [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) — Сетевая защита, управление доступом, мониторинг и реагирование. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «Сетевая защита, Управление доступом, Мониторинг и реагирование»
- [Аудиты и обследования](https://cns-corp.ru/it-uslugi/infrastruktura/audit/) — Аудит распределённой сети складов, ЦОД и филиалов. _(кейс)_
  - кейс [Модернизация КСПД и создание отказоустойчивого ядра сети в распределённом ЦОД](https://cns-corp.ru/kejsy/modernizacziya-kspd-i-sozdanie-otkazoustojchivogo-yadra-seti-v-raspredelyonnom-czod/)

**[Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)**

- [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/) — Аэропорты, вокзалы, морские порты, склады, платные автотрассы и стоянки. _(сайт)_
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/): «аэропортам, железнодорожным вокзалам, морским портам»
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/): «промышленным предприятиям, складам»
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/): «платным автотрассам и стоянкам»
- [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/) — Распознавание номерных знаков транспорта, контроль территории онлайн. _(сайт)_
  - [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/): «Распознавание лиц и номерных знаков, проезжающих транспортных средств»

**[ИТ-поддержка](https://cns-corp.ru/it-uslugi/it-support/)**

- [IT-аутсорсинг](https://cns-corp.ru/it-uslugi/it-support/it-autsorsing/) — Поддержка распределённой сетевой инфраструктуры. _(кейс)_
  - кейс [Техническая поддержка распределённой сетевой инфраструктуры на базе Cisco для федерального дистрибьютора](https://cns-corp.ru/kejsy/tehnicheskaya-podderzhka-raspredelennoi-setevoi-infrastruktury-na-baze-cisco-dlya-federalnogo-distribyutora/)

**[Сети](https://cns-corp.ru/it-uslugi/seti/)**

- [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/) — Стабильная сеть на складах для терминалов и мобильных устройств. _(сайт)_
  - [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/): «(офисы, склады, торговые пространства)»
- [Радиообследование Wi-Fi](https://cns-corp.ru/it-uslugi/seti/radioobsledovanie-wi-fi/) — Обследование складов с высокими стеллажами. _(сайт)_
  - [Радиообследование Wi-Fi](https://cns-corp.ru/it-uslugi/seti/radioobsledovanie-wi-fi/): «склады с высокими стеллажами»
- [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/) — Связь на объектах транспортной инфраструктуры. _(сайт)_
  - [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/): «объектам инфраструктуры (транспорт, энергетика)»
- [Сетевые решения](https://cns-corp.ru/it-uslugi/seti/setevye-resheniya/) — Сеть для складского комплекса, ЦОД и филиалов. _(кейс)_
  - кейс [Модернизация КСПД и создание отказоустойчивого ядра сети в распределённом ЦОД](https://cns-corp.ru/kejsy/modernizacziya-kspd-i-sozdanie-otkazoustojchivogo-yadra-seti-v-raspredelyonnom-czod/)
  - кейс [Техническая поддержка распределённой сетевой инфраструктуры на базе Cisco для федерального дистрибьютора](https://cns-corp.ru/kejsy/tehnicheskaya-podderzhka-raspredelennoi-setevoi-infrastruktury-na-baze-cisco-dlya-federalnogo-distribyutora/)

**[Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/)**

- [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/) — Расписания и объявления на вокзалах, в аэропортах и диспетчерских. _(сайт)_
  - [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/): «Объектам транспортной и городской инфраструктуры (вокзалы, аэропорты, диспетчерские)»
- [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/) — Табло и навигация на транспортных объектах. _(сайт)_
  - [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/): «Транспортная и городская инфраструктура: Вокзалы, аэропорты»
- [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/) — Видеостены в аэропортах. _(сайт)_
  - [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/): «аэропорты»

**[Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)**

- [Почтовый сервис](https://cns-corp.ru/it-uslugi/kommunikaczii/pochtovyj-servis/) — Единый цифровой офис для офиса, складов и водителей. _(кейс)_
  - кейс [CNS обеспечил бесшовный переход ФК «Пульс» на единый цифровой офис с Яндекс 360](https://cns-corp.ru/kejsy/cns-obespechil-besshovnyi-perehod-fk-puls-na-edinyi-czifrovoi-ofis-s-yandeks-360/)

**Кейсы:** 

- [Техническая поддержка распределённой сетевой инфраструктуры на базе Cisco для федерального дистрибьютора](https://cns-corp.ru/kejsy/tehnicheskaya-podderzhka-raspredelennoi-setevoi-infrastruktury-na-baze-cisco-dlya-federalnogo-distribyutora/) — Федеральный дистрибьютор товаров для бизнеса: поддержка распределённой сети Cisco со строгими требованиями к реакции.
- [Модернизация КСПД и создание отказоустойчивого ядра сети в распределённом ЦОД](https://cns-corp.ru/kejsy/modernizacziya-kspd-i-sozdanie-otkazoustojchivogo-yadra-seti-v-raspredelyonnom-czod/) — Производственно-логистический холдинг: офис, производство, склад, ЦОД и более десяти филиалов. Аудит сети, новое ядро и управление сетевой безопасностью.
- [CNS обеспечил бесшовный переход ФК «Пульс» на единый цифровой офис с Яндекс 360](https://cns-corp.ru/kejsy/cns-obespechil-besshovnyi-perehod-fk-puls-na-edinyi-czifrovoi-ofis-s-yandeks-360/) — Миграция 1600 сотрудников на Яндекс 360 без простоев; дальше — до 5000 пользователей, включая склады и водителей.

## 5. Строительство

Защищаем документооборот и корпоративную сеть, строим телекоммуникационную инфраструктуру новых бизнес-центров и жилых комплексов.

**[Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)**

- [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) — Безопасный документооборот, сетевая безопасность, антивирусная защита, управление доступом. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «Безопасный документооборот, Сетевая безопасность, Антивирусная защита, Управление доступом»
  - кейс [От пилота до промышленной эксплуатации: как CNS помог крупному дилеру спецтехники выстроить систему кибербезопасности](https://cns-corp.ru/kejsy/ot-pilota-do-promyshlennoj-ekspluataczii-kak-cns-pomog-krupnomu-dileru-specztehniki-vystroit-sistemu-kiberbezopasnosti/)

**[Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)**

- [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/) — Контроль строительных площадок и техники. _(**рекомендация**)_
  - нет на сайте — Отраслевой связи на сайте нет.
- [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/) — Пропускной режим для людей и транспорта на площадке. _(**рекомендация**)_
  - нет на сайте — СКУД — «управление и контроль транспорта и людей на охраняемую территорию»; стройплощадки не названы.

**[Сети](https://cns-corp.ru/it-uslugi/seti/)**

- [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/) — Телекоммуникационная инфраструктура в новых бизнес-центрах и жилых комплексах. _(сайт)_
  - [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/): «Застройщикам и управляющим компаниям для создания современной телекоммуникационной инфраструктуры в новых бизнес-центрах и жилых комплексах»
- [Монтаж СКС](https://cns-corp.ru/it-uslugi/seti/montazh-sks/) — Единая кабельная система для сети, телефонии, СКУД и видеонаблюдения в новом здании. _(**рекомендация**)_
  - нет на сайте — Страница СКС — про новые здания и интеграцию систем в единую среду; застройщики не названы.

**[Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/)**

- [Система управления под ключ](https://cns-corp.ru/it-uslugi/multimedia/sistema-umnogo-doma/) — Системы управления для жилых комплексов и бизнес-центров. _(**рекомендация**)_
  - нет на сайте — Страница описывает систему «в Вашем жилье или офисе»; застройщики не названы.

**[Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)**

- [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/) — Связь штаба с удалёнными объектами. _(**рекомендация**)_
  - нет на сайте — Отраслевой связи на сайте нет.

**Кейсы:** 

- [От пилота до промышленной эксплуатации: как CNS помог крупному дилеру спецтехники выстроить систему кибербезопасности](https://cns-corp.ru/kejsy/ot-pilota-do-promyshlennoj-ekspluataczii-kak-cns-pomog-krupnomu-dileru-specztehniki-vystroit-sistemu-kiberbezopasnosti/) — Межсетевое экранирование для дилера дорожно-строительной техники — от пилота до промышленной эксплуатации.

## 6. Образование

Делаем вход безопасным, оснащаем аудитории и актовые залы, организуем дистанционное обучение и защищаем данные учащихся.

**[Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)**

- [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) — Защита персональных данных, контентная фильтрация и веб-безопасность, защита конечных точек, обучение и симуляция атак. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «Защита персональных данных, Контентная фильтрация и веб-безопасность, Защита конечных точек, Обучение и симуляция атак»
- [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/) — Российские платформы телефонии и коммуникаций для школ и университетов. _(сайт)_
  - [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/): «для компаний, школ, университетов, госструктур и корпораций»

**[Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)**

- [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/) — Контроль доступа для вузов, школ и детских садов, интеграция с турникетами. _(сайт)_
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/): «вузам, школам, дошкольным учреждениям»
  - кейс [Внедрение системы термометрии и контроля доступа для школы «Летово»](https://cns-corp.ru/kejsy/it-autstaffing/)
- [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/) — Камеры с измерением температуры на входе. _(кейс)_
  - кейс [Внедрение системы термометрии и контроля доступа для школы «Летово»](https://cns-corp.ru/kejsy/it-autstaffing/)

**[Сети](https://cns-corp.ru/it-uslugi/seti/)**

- [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/) — Wi-Fi для учебных корпусов. _(**рекомендация**)_
  - нет на сайте — Отраслевой связи на сайте нет.

**[Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/)**

- [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/) — Учебные аудитории, лекционные залы, кабинеты и научные центры. _(сайт)_
  - [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/): «Образовательные и научные учреждения: Учебные аудитории, лекционные залы»
  - [Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/): «Образовательные учреждения (вузы, школы, учебные центры)»
- [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/) — Интерактивные доски и сенсорные панели в классах. _(сайт)_
  - [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/): «Образовательным учреждениям и учебным центрам для оснащения классов интерактивными досками»
- [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/) — Видеостены для аудиторий и лабораторий. _(сайт)_
  - [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/): «Образовательных и научных учреждений (учебные аудитории, лаборатории)»
- [Сценическое оборудование](https://cns-corp.ru/it-uslugi/multimedia/sczenicheskoe-oborudovanie/) — Актовые залы под ключ: сцена, кулисы, мультимедиа. _(кейс)_
  - кейс [Оборудование актового зала в школе](https://cns-corp.ru/kejsy/oborudovanie-aktovogo-zala-v-shkole/)
- [Акустические системы](https://cns-corp.ru/it-uslugi/multimedia/akusticheskie-sistemy/) — Звук для актового зала: микшерная консоль, акустика. _(кейс)_
  - кейс [Оборудование актового зала в школе](https://cns-corp.ru/kejsy/oborudovanie-aktovogo-zala-v-shkole/)
- [Световое оборудование](https://cns-corp.ru/it-uslugi/multimedia/svetovoe-oborudovanie/) — Сценический свет для актовых залов. _(**рекомендация**)_
  - нет на сайте — Страница светового оборудования — об оснащении сцен; в кейсе актового зала свет не упомянут.

**[Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)**

- [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/) — Дистанционное обучение и лекции преподавателей из других городов. _(сайт)_
  - [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/): «образование – дистанционное обучение»
- [Телефония](https://cns-corp.ru/it-uslugi/kommunikaczii/telefoniya/) — IP-телефония для школ и университетов. _(сайт)_
  - [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/): «для компаний, школ, университетов, госструктур и корпораций»

**Кейсы:** 

- [Внедрение системы термометрии и контроля доступа для школы «Летово»](https://cns-corp.ru/kejsy/it-autstaffing/) — Камеры с измерением температуры, интеграция с турникетами и автоматическая блокировка доступа.
- [Оборудование актового зала в школе](https://cns-corp.ru/kejsy/oborudovanie-aktovogo-zala-v-shkole/) — Поставка, монтаж и настройка мультимедиа, цифровая микшерная консоль, моторизированные кулисы.

## 7. Медицина

Защищаем данные пациентов, подключаем телемедицину, оснащаем аптеки и поддерживаем распределённую инфраструктуру по SLA.

**[Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)**

- [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) — Контроль доступа, DLP, анализ трафика и выявление аномалий, шифрование данных, аудит уязвимостей. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «Контроль доступа, Защита от утечек данных (DLP), Анализ трафика и выявление аномалий, Шифрование данных, Аудит уязвимостей»

**[Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)**

- [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/) — Контроль доступа в медицинских учреждениях. _(сайт)_
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/): «медицинским учреждениям»

**[ИТ-поддержка](https://cns-corp.ru/it-uslugi/it-support/)**

- [IT-аутсорсинг](https://cns-corp.ru/it-uslugi/it-support/it-autsorsing/) — Поддержка распределённой инфраструктуры и сети аптек. _(кейс)_
  - кейс [CNS заключил трёхлетний контракт на комплексную техническую поддержку распределённой ИТ-инфраструктуры крупной фармацевтической компании](https://cns-corp.ru/kejsy/cns-zaklyuchil-tryohletnij-kontrakt-na-kompleksnuyu-tehnicheskuyu-podderzhku-raspredelyonnoj-it-infrastruktury-krupnoj-farmaczevticheskoj-kompanii/)
  - кейс [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/)
- [Расширенная гарантия](https://cns-corp.ru/it-uslugi/rasshirennaya-garantiya/) — Сервис серверов и СХД с ЗИП и выездом инженера. _(кейс)_
  - кейс [CNS заключил трёхлетний контракт на комплексную техническую поддержку распределённой ИТ-инфраструктуры крупной фармацевтической компании](https://cns-corp.ru/kejsy/cns-zaklyuchil-tryohletnij-kontrakt-na-kompleksnuyu-tehnicheskuyu-podderzhku-raspredelyonnoj-it-infrastruktury-krupnoj-farmaczevticheskoj-kompanii/)

**[Сети](https://cns-corp.ru/it-uslugi/seti/)**

- [Монтаж СКС](https://cns-corp.ru/it-uslugi/seti/montazh-sks/) — Кабельная система для аптек под ТВ и инфокиоски. _(кейс)_
  - кейс [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/)

**[Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/)**

- [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/) — Обучение, диагностика (системы визуализации), консультирование пациентов. _(сайт)_
  - [Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/): «Медицинские учреждения для применения в обучении (анимационные модели органов), диагностике (системы визуализации) и консультировании пациентов»
  - кейс [Оснащение видео- и аудиооборудованием учебного класса для «Generium»](https://cns-corp.ru/kejsy/osnashhenie-video-i-audio-oborudovaniem-uchebnogo-klassa-dlya-generium/)
- [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/) — Рекламные ТВ и инфокиоски в аптеках. _(кейс)_
  - кейс [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/)
- [Акустические системы](https://cns-corp.ru/it-uslugi/multimedia/akusticheskie-sistemy/) — Аудиооснащение учебных классов и переговорных. _(кейс)_
  - кейс [Оснащение видео- и аудиооборудованием учебного класса для «Generium»](https://cns-corp.ru/kejsy/osnashhenie-video-i-audio-oborudovaniem-uchebnogo-klassa-dlya-generium/)

**[Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)**

- [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/) — Телемедицина: удалённые консультации пациентов; ВКС в переговорных. _(сайт)_
  - [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/): «телемедицина – удалённая консультация пациентов»
  - кейс [Комплексное оснащение переговорных комнат для «Generium»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-peregovornyh-komnat-dlya-generium/)
- [Почтовый сервис](https://cns-corp.ru/it-uslugi/kommunikaczii/pochtovyj-servis/) — Корпоративная почта и единый цифровой офис. _(кейс)_
  - кейс [CNS обеспечил бесшовный переход ФК «Пульс» на единый цифровой офис с Яндекс 360](https://cns-corp.ru/kejsy/cns-obespechil-besshovnyi-perehod-fk-puls-na-edinyi-czifrovoi-ofis-s-yandeks-360/)

**Кейсы:** 

- [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/) — Федеральная сеть аптек (800+ точек): кабельная система под рекламные ТВ и инфокиоски в 500+ аптеках, затем ИТ-обслуживание.
- [CNS заключил трёхлетний контракт на комплексную техническую поддержку распределённой ИТ-инфраструктуры крупной фармацевтической компании](https://cns-corp.ru/kejsy/cns-zaklyuchil-tryohletnij-kontrakt-na-kompleksnuyu-tehnicheskuyu-podderzhku-raspredelyonnoj-it-infrastruktury-krupnoj-farmaczevticheskoj-kompanii/) — Поддержка 24/7 более 50 единиц серверов, СХД и сетевого оборудования: реакция до 4 часов, выезд, ЗИП.
- [Комплексное оснащение переговорных комнат для «Generium»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-peregovornyh-komnat-dlya-generium/) — ВКС в переговорных — в офисе и на производственных площадках фармкомпании.
- [Оснащение видео- и аудиооборудованием учебного класса для «Generium»](https://cns-corp.ru/kejsy/osnashhenie-video-i-audio-oborudovaniem-uchebnogo-klassa-dlya-generium/) — Акустический расчёт, акустические панели, проектор и моторизированный экран.
- [CNS обеспечил бесшовный переход ФК «Пульс» на единый цифровой офис с Яндекс 360](https://cns-corp.ru/kejsy/cns-obespechil-besshovnyi-perehod-fk-puls-na-edinyi-czifrovoi-ofis-s-yandeks-360/) — Миграция 1600 сотрудников на Яндекс 360 без простоев; дальше — до 5000 пользователей, включая склады и водителей.

## 8. Социальная сфера

Переводим учреждения на отечественный стек, защищаем данные, оснащаем ситуационные центры, залы и общественные пространства.

**[Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)**

- [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) — Защита конфиденциальных данных, антифишинг, контроль инфраструктуры подрядчиков, киберстрахование, повышение осведомлённости. _(сайт)_
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/): «Защита конфиденциальных данных, Антифишинг и защита от социальной инженерии, Контроль инфраструктуры подрядчиков, Киберстрахование, Повышение осведомленности»
- [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/) — Отечественные ОС (Astra Linux — стандарт ФОИВов) и облака для госучреждений. _(сайт)_
  - [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/): «ОС Astra Linux – стандарт ФОИВов»
  - [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/): «Облачные сервисы для бизнеса и госучреждений»

**[Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)**

- [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/) — Контроль доступа в государственных структурах и ведомствах. _(сайт)_
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/): «государственным структурам и ведомствам»

**[Сети](https://cns-corp.ru/it-uslugi/seti/)**

- [Сетевые решения](https://cns-corp.ru/it-uslugi/seti/setevye-resheniya/) — Сетевая инфраструктура стадионов и общественных объектов. _(сайт)_
  - [Сетевые решения](https://cns-corp.ru/it-uslugi/seti/setevye-resheniya/): «торгового центра, стадиона»

**[Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/)**

- [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/) — Ситуационные и кризисные центры; конгресс-холлы, спорткомплексы, музеи и выставки. _(сайт)_
  - [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/): «Государственные и оперативные службы: Ситуационные, диспетчерские и кризисные центры»
  - [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/): «Общественные пространства: Конгресс-холлы, спортивные комплексы»
  - [Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/): «Культурные и общественные пространства (музеи, выставки»
- [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/) — Диспетчерские, кризисные центры, МЧС; спортивные арены. _(сайт)_
  - [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/): «Государственных и оперативных служб (диспетчерские, кризисные центры, МЧС)»
  - [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/): «спортивные арены»
- [Акустические системы](https://cns-corp.ru/it-uslugi/multimedia/akusticheskie-sistemy/) — Концертные залы, клубы, конференц-центры. _(сайт)_
  - [Акустические системы](https://cns-corp.ru/it-uslugi/multimedia/akusticheskie-sistemy/): «концертных залов, клубов, конференц-центров»
- [Сценическое оборудование](https://cns-corp.ru/it-uslugi/multimedia/sczenicheskoe-oborudovanie/) — Сцены домов культуры, театров и концертных залов. _(**рекомендация**)_
  - нет на сайте — Концертные залы названы на странице акустики; на странице сценического оборудования отраслей нет.
- [Световое оборудование](https://cns-corp.ru/it-uslugi/multimedia/svetovoe-oborudovanie/) — Сценический свет для залов. _(**рекомендация**)_
  - нет на сайте — Отраслевой связи на сайте нет; логично в паре со сценой и звуком.

**[Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)**

- [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/) — ВКС для государственного сектора. _(сайт)_
  - [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/): «государственный и промышленный сектор»
- [Телефония](https://cns-corp.ru/it-uslugi/kommunikaczii/telefoniya/) — IP-телефония для госструктур на российских платформах. _(сайт)_
  - [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/): «для компаний, школ, университетов, госструктур и корпораций»

**Кейсы:** на сайте нет.


## Решение → отрасли

Обратная проверка: в каких отраслях стоит каждое решение. ✓ — сайт или кейс, ◌ — рекомендация.

| Направление | Решение | Отрасли |
|---|---|---|
| Инфраструктура | [Аудиты и обследования](https://cns-corp.ru/it-uslugi/infrastruktura/audit/) | ✓ Производство, ✓ Логистика |
| Инфраструктура | [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/) | ✓ Ритейл и FMCG, ✓ Агропромышленный комплекс, ✓ Производство, ✓ Логистика, ✓ Строительство, ✓ Образование, ✓ Медицина, ✓ Социальная сфера |
| Инфраструктура | [Серверные решения](https://cns-corp.ru/it-uslugi/infrastruktura/servernye-resheniya/) | ✓ Ритейл и FMCG, ✓ Производство |
| Инфраструктура | [Виртуализация](https://cns-corp.ru/it-uslugi/infrastruktura/virtualizacziya/) | ✓ Производство |
| Инфраструктура | [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/) | ✓ Производство, ✓ Образование, ✓ Социальная сфера |
| Инфраструктура | [IT-переезд, открытие офисов](https://cns-corp.ru/it-uslugi/infrastruktura/it-pereezd/) | _для любой отрасли_ |
| Инфраструктура | [AutoID: учёт и маркировка](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/) | ✓ Ритейл и FMCG, ◌ Агропромышленный комплекс, ✓ Производство, ✓ Логистика |
| Облачные сервисы | [Аренда серверных стоек](https://cns-corp.ru/it-uslugi/oblachnye-servisy/arenda-stoek/) | ✓ Агропромышленный комплекс |
| Облачные сервисы | [Виртуальные серверы](https://cns-corp.ru/it-uslugi/oblachnye-servisy/virtualnye/) | ✓ Агропромышленный комплекс |
| Безопасность | [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/) | ✓ Ритейл и FMCG, ✓ Производство, ✓ Логистика, ◌ Строительство, ✓ Образование, ✓ Медицина, ✓ Социальная сфера |
| Безопасность | [Пожарная безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/pozharnaya-bezopasnost/) | _для любой отрасли_ |
| Безопасность | [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/) | ✓ Ритейл и FMCG, ◌ Агропромышленный комплекс, ✓ Производство, ✓ Логистика, ◌ Строительство, ✓ Образование |
| ИТ-поддержка | [IT-аутсорсинг](https://cns-corp.ru/it-uslugi/it-support/it-autsorsing/) | ✓ Ритейл и FMCG, ✓ Агропромышленный комплекс, ✓ Логистика, ✓ Медицина |
| ИТ-поддержка | [IT-аутстаффинг](https://cns-corp.ru/it-uslugi/it-support/it-autstaffing/) | _для любой отрасли_ |
| ИТ-поддержка | [Расширенная гарантия](https://cns-corp.ru/it-uslugi/rasshirennaya-garantiya/) | ✓ Производство, ✓ Медицина |
| Сети | [Сетевые решения](https://cns-corp.ru/it-uslugi/seti/setevye-resheniya/) | ✓ Ритейл и FMCG, ✓ Производство, ✓ Логистика, ✓ Социальная сфера |
| Сети | [Монтаж СКС](https://cns-corp.ru/it-uslugi/seti/montazh-sks/) | ✓ Ритейл и FMCG, ✓ Производство, ◌ Строительство, ✓ Медицина |
| Сети | [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/) | ◌ Агропромышленный комплекс, ✓ Производство, ✓ Логистика, ✓ Строительство |
| Сети | [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/) | ✓ Ритейл и FMCG, ◌ Агропромышленный комплекс, ◌ Производство, ✓ Логистика, ◌ Образование |
| Сети | [Радиообследование Wi-Fi](https://cns-corp.ru/it-uslugi/seti/radioobsledovanie-wi-fi/) | ✓ Ритейл и FMCG, ✓ Производство, ✓ Логистика |
| Мультимедиа | [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/) | ✓ Ритейл и FMCG, ✓ Логистика, ✓ Образование, ✓ Медицина, ✓ Социальная сфера |
| Мультимедиа | [Бронирование переговорных комнат](https://cns-corp.ru/it-uslugi/multimedia/sistema-bronirovaniya-peregovornyh-komnat/) | _для любой отрасли_ |
| Мультимедиа | [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/) | ✓ Ритейл и FMCG, ✓ Логистика, ✓ Образование, ✓ Медицина |
| Мультимедиа | [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/) | ✓ Ритейл и FMCG, ◌ Производство, ✓ Логистика, ✓ Образование, ✓ Социальная сфера |
| Мультимедиа | [Акустические системы](https://cns-corp.ru/it-uslugi/multimedia/akusticheskie-sistemy/) | ✓ Образование, ✓ Медицина, ✓ Социальная сфера |
| Мультимедиа | [Сценическое оборудование](https://cns-corp.ru/it-uslugi/multimedia/sczenicheskoe-oborudovanie/) | ✓ Образование, ◌ Социальная сфера |
| Мультимедиа | [Световое оборудование](https://cns-corp.ru/it-uslugi/multimedia/svetovoe-oborudovanie/) | ◌ Образование, ◌ Социальная сфера |
| Мультимедиа | [Система управления под ключ](https://cns-corp.ru/it-uslugi/multimedia/sistema-umnogo-doma/) | ◌ Строительство |
| Коммуникации | [Телефония](https://cns-corp.ru/it-uslugi/kommunikaczii/telefoniya/) | ✓ Образование, ✓ Социальная сфера |
| Коммуникации | [Почтовый сервис](https://cns-corp.ru/it-uslugi/kommunikaczii/pochtovyj-servis/) | ✓ Логистика, ✓ Медицина |
| Коммуникации | [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/) | ✓ Ритейл и FMCG, ◌ Агропромышленный комплекс, ✓ Производство, ◌ Строительство, ✓ Образование, ✓ Медицина, ✓ Социальная сфера |

## Кейсы

| Кейс | Отрасли | Решения в кейсе |
|---|---|---|
| [Комплексное оснащение сети фешн-магазинов ООО «ПикНик»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-seti-feshn-magazinov-ooo-piknik/) | Ритейл и FMCG | Серверные решения, Сетевые решения, Монтаж СКС, Видеонаблюдение, СКУД |
| [IT-аутсорсинг. Техническая поддержка](https://cns-corp.ru/kejsy/it-autsorsing-tehnicheskaya-podderzhka/) | Ритейл и FMCG | IT-аутсорсинг |
| [Монтаж кабельной системы](https://cns-corp.ru/kejsy/montazh-kabelnoj-sistemy/) | Ритейл и FMCG, Медицина | Монтаж СКС, Информационно-рекламные экраны, IT-аутсорсинг |
| [Поставка и настройка серверного оборудования Supermicro и Mikrotik](https://cns-corp.ru/kejsy/postavka-i-nastrojka-servernogo-oborudovaniya-supermicro-i-mikrotik/) | Ритейл и FMCG | Серверные решения, Сетевые решения |
| [Годовой контракт на 21 юрлицо: как команда CNS выстроила систему оснащения рабочих мест для крупного рыбопромышленного холдинга](https://cns-corp.ru/kejsy/godovoi-kontrakt-na-21-yurliczo-kak-komanda-cns-vystroila-sistemu-osnashheniya-rabochih-mest-dlya-krupnogo-rybopromyshlennogo-holdinga/) | Агропромышленный комплекс | — (поставка рабочих мест — раздел «Оборудование») |
| [Модернизация систем видеонаблюдения и контроля доступа (СКУД) для лидера нефтегазовой отрасли](https://cns-corp.ru/kejsy/modernizacziya-sistem-videonablyudeniya-i-kontrolya-dostupa-skud-dlya-lidera-neftegazovoi-otrasli/) | Производство | Видеонаблюдение, СКУД, Импортозамещение в ИТ |
| [Построение отказоустойчивой ИТ-инфраструктуры хранения и резервного копирования для горнодобывающей компании «Покровский рудник»](https://cns-corp.ru/kejsy/postroenie-otkazoustojchivoj-it-infrastruktury-hraneniya-i-rezervnogo-kopirovaniya-dlya-gornodobyvayushhej-kompanii-pokrovskij-rudnik/) | Производство | Серверные решения |
| [Развёртывание серверной инфраструктуры и СХД для нового ЦОД легкой промышленности](https://cns-corp.ru/kejsy/razvertyvanie-servernoi-infrastruktury-i-shd-dlya-novogo-czod-legkoi-promyshlennosti/) | Производство | Серверные решения, Расширенная гарантия |
| [Импортозамещение серверной инфраструктуры и платформы виртуализации на государственном уровне](https://cns-corp.ru/kejsy/importozameshhenie-servernoj-infrastruktury-i-platformy-virtualizaczii-na-gosudarstvennom-urovne/) | Производство | Импортозамещение в ИТ, Серверные решения, Виртуализация |
| [Модернизация КСПД и создание отказоустойчивого ядра сети в распределённом ЦОД](https://cns-corp.ru/kejsy/modernizacziya-kspd-i-sozdanie-otkazoustojchivogo-yadra-seti-v-raspredelyonnom-czod/) | Производство, Логистика | Аудиты и обследования, Сетевые решения, Услуги ИБ |
| [Техническая поддержка распределённой сетевой инфраструктуры на базе Cisco для федерального дистрибьютора](https://cns-corp.ru/kejsy/tehnicheskaya-podderzhka-raspredelennoi-setevoi-infrastruktury-na-baze-cisco-dlya-federalnogo-distribyutora/) | Логистика | IT-аутсорсинг, Сетевые решения |
| [CNS обеспечил бесшовный переход ФК «Пульс» на единый цифровой офис с Яндекс 360](https://cns-corp.ru/kejsy/cns-obespechil-besshovnyi-perehod-fk-puls-na-edinyi-czifrovoi-ofis-s-yandeks-360/) | Логистика, Медицина | Почтовый сервис |
| [От пилота до промышленной эксплуатации: как CNS помог крупному дилеру спецтехники выстроить систему кибербезопасности](https://cns-corp.ru/kejsy/ot-pilota-do-promyshlennoj-ekspluataczii-kak-cns-pomog-krupnomu-dileru-specztehniki-vystroit-sistemu-kiberbezopasnosti/) | Строительство | Услуги ИБ |
| [Внедрение системы термометрии и контроля доступа для школы «Летово»](https://cns-corp.ru/kejsy/it-autstaffing/) | Образование | СКУД, Видеонаблюдение |
| [Оборудование актового зала в школе](https://cns-corp.ru/kejsy/oborudovanie-aktovogo-zala-v-shkole/) | Образование | Сценическое оборудование, Акустические системы |
| [CNS заключил трёхлетний контракт на комплексную техническую поддержку распределённой ИТ-инфраструктуры крупной фармацевтической компании](https://cns-corp.ru/kejsy/cns-zaklyuchil-tryohletnij-kontrakt-na-kompleksnuyu-tehnicheskuyu-podderzhku-raspredelyonnoj-it-infrastruktury-krupnoj-farmaczevticheskoj-kompanii/) | Медицина | IT-аутсорсинг, Расширенная гарантия |
| [Комплексное оснащение переговорных комнат для «Generium»](https://cns-corp.ru/kejsy/kompleksnoe-osnashhenie-peregovornyh-komnat-dlya-generium/) | Медицина | Видеоконференцсвязь |
| [Оснащение видео- и аудиооборудованием учебного класса для «Generium»](https://cns-corp.ru/kejsy/osnashhenie-video-i-audio-oborudovaniem-uchebnogo-klassa-dlya-generium/) | Медицина | Акустические системы, Системы отображения информации |

Не отнесены ни к одной из 8 отраслей: «Установка видеостен для РЕН-ТВ» (медиа), «Создание ИТ-инфраструктуры „с нуля“ для компании в сфере звукозаписи…», «Масштабный проект CNS: разделение ИТ-инфраструктуры и оснащение третьего ЦОД…» (диверсифицированный холдинг), «Услуги первой линии поддержки, диспетчеризация 24/7» и «Услуги по выездному IT-обслуживанию» (корпоративное питание).

## Что нашлось на сайте по ходу проверки

- Хаб `/industries/` отдаёт 404, `/industries/logistics/` — 404. Остальные 7 отраслевых страниц — заготовки: lorem ipsum, «Предприятие 1/2/3», одинаковый блок «Решения для учреждений».
- «Сценическое оборудование»: вводный текст скопирован со страницы бронирования переговорных («…системы бронирования комнат, конференц-залов…»).
- «Почтовый сервис»: на странице блоки про IP-телефонию и монтаж ВОЛС.
- «Сценическое оборудование» и «Система управления под ключ»: в блоке «Как мы работаем» — текст про телефонию и мини-АТС.
- «Световое оборудование»: заголовок «Что входит в услугу оснащения сцен под ключ» повторён пять раз.
- Название расходится: в меню и H1 — «Система управления под ключ», в карточке на странице «Услуги» — «Система умного дома»; адрес `/sistema-umnogo-doma/`.
- Кейс «Летово» лежит по адресу `/kejsy/it-autstaffing/` (адрес от другого кейса).
- Кейс «Оборудование актового зала в школе» открывается, но в списке «Проекты» его нет.
- ФК «Пульс» в кейсе — фармдистрибьютор (офис, склады, водители), не спортивный клуб.

## Справочник: меню «Услуги»

- [Инфраструктура](https://cns-corp.ru/it-uslugi/infrastruktura/)
  - [Аудиты и обследования](https://cns-corp.ru/it-uslugi/infrastruktura/audit/)
  - [Услуги ИБ](https://cns-corp.ru/it-uslugi/infrastruktura/informaczionnaya-bezopasnost/)
  - [Серверные решения](https://cns-corp.ru/it-uslugi/infrastruktura/servernye-resheniya/)
  - [Виртуализация](https://cns-corp.ru/it-uslugi/infrastruktura/virtualizacziya/)
  - [Импортозамещение в ИТ](https://cns-corp.ru/it-uslugi/infrastruktura/importozameshhenie/)
  - [IT-переезд, открытие офисов](https://cns-corp.ru/it-uslugi/infrastruktura/it-pereezd/)
  - [AutoID: учёт и маркировка](https://cns-corp.ru/it-uslugi/infrastruktura/autoid/)
- [Облачные сервисы](https://cns-corp.ru/it-uslugi/oblachnye-servisy/)
  - [Аренда серверных стоек](https://cns-corp.ru/it-uslugi/oblachnye-servisy/arenda-stoek/)
  - [Виртуальные серверы](https://cns-corp.ru/it-uslugi/oblachnye-servisy/virtualnye/)
- [Безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/)
  - [СКУД](https://cns-corp.ru/it-uslugi/bezopasnost/skud/)
  - [Пожарная безопасность](https://cns-corp.ru/it-uslugi/bezopasnost/pozharnaya-bezopasnost/)
  - [Видеонаблюдение](https://cns-corp.ru/it-uslugi/bezopasnost/videonablyudenie/)
- [ИТ-поддержка](https://cns-corp.ru/it-uslugi/it-support/)
  - [IT-аутсорсинг](https://cns-corp.ru/it-uslugi/it-support/it-autsorsing/)
  - [IT-аутстаффинг](https://cns-corp.ru/it-uslugi/it-support/it-autstaffing/)
  - [Расширенная гарантия](https://cns-corp.ru/it-uslugi/rasshirennaya-garantiya/)
- [Сети](https://cns-corp.ru/it-uslugi/seti/)
  - [Сетевые решения](https://cns-corp.ru/it-uslugi/seti/setevye-resheniya/)
  - [Монтаж СКС](https://cns-corp.ru/it-uslugi/seti/montazh-sks/)
  - [Монтаж ВОЛС](https://cns-corp.ru/it-uslugi/seti/montazh-vols/)
  - [Построение Wi-Fi сетей](https://cns-corp.ru/it-uslugi/seti/postroenie-wi-fi-setej/)
  - [Радиообследование Wi-Fi](https://cns-corp.ru/it-uslugi/seti/radioobsledovanie-wi-fi/)
- [Мультимедиа](https://cns-corp.ru/it-uslugi/multimedia/)
  - [Системы отображения информации](https://cns-corp.ru/it-uslugi/multimedia/sistemy-otobrazheniya-informaczii/)
  - [Бронирование переговорных комнат](https://cns-corp.ru/it-uslugi/multimedia/sistema-bronirovaniya-peregovornyh-komnat/)
  - [Информационно-рекламные экраны](https://cns-corp.ru/it-uslugi/multimedia/informaczionno-reklamnye-ekrany/)
  - [Видеостены](https://cns-corp.ru/it-uslugi/multimedia/videosteny/)
  - [Акустические системы](https://cns-corp.ru/it-uslugi/multimedia/akusticheskie-sistemy/)
  - [Сценическое оборудование](https://cns-corp.ru/it-uslugi/multimedia/sczenicheskoe-oborudovanie/)
  - [Световое оборудование](https://cns-corp.ru/it-uslugi/multimedia/svetovoe-oborudovanie/)
  - [Система управления под ключ](https://cns-corp.ru/it-uslugi/multimedia/sistema-umnogo-doma/)
- [Коммуникации](https://cns-corp.ru/it-uslugi/kommunikaczii/)
  - [Телефония](https://cns-corp.ru/it-uslugi/kommunikaczii/telefoniya/)
  - [Почтовый сервис](https://cns-corp.ru/it-uslugi/kommunikaczii/pochtovyj-servis/)
  - [Видеоконференцсвязь](https://cns-corp.ru/it-uslugi/kommunikaczii/videokonferenczsvyaz/)
