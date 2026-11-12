# Инструменты паспорта: запуск и проверка на macOS

Основное приложение устанавливается по [HANDOFF.md](../HANDOFF.md). Дополнения соответствуют восьми практикам [паспорта](../Паспорт%20СППР.md). Все цены демонстрационные. Внешние аккаунты, публикация проекта и платные подписки не требуются.

## AHP в Python и Excel

Готовая книга: [AHP.xlsx](../reports/passport/AHP.xlsx). Откройте ее в Microsoft Excel; на листе «Расчет» изменяются шесть желтых ячеек верхнего треугольника B5:E8. Нижний треугольник, степенные итерации, CI/CR и ранги вычисляются формулами. Включите автоматический пересчет Excel, если он отключен. Исходные веса на листе «Результат» — 45.5408%, 26.2833%, 14.0880%, 14.0880%; CR=0.003838112. Измените C5 с 2 на 5: веса должны измениться, затем восстановите 2. Если CR>0.10, пересмотрите противоречивые сравнения.

Книга проверена расчетным движком и чтением сохраненных формул/значений; запуск нативного Microsoft Excel на этой машине не выполнялся. На листе приведены источник метода и статус учебных исходных данных. Учебный AHP-синтез и абсолютный балл рабочего маршрутизатора имеют разные нормировки; их нельзя непосредственно сравнивать.

В Streamlit выберите «AHP: парные сравнения», заполните шесть отношений. При отправке формы веса вычисляются из матрицы; поля баллов при этом не используются. Несогласованная матрица блокирует расчет. Для CLI:

```bash
.venv/bin/python -m travel_dss.cli validate-request examples/ahp_request.json
.venv/bin/python -m travel_dss.cli recommend --request examples/ahp_request.json \
  --allow-demo --backend pulp --no-cache --output reports/my-ahp-pulp.json \
  --html reports/my-ahp-pulp.html
```

## PuLP / CBC и дерево решений

PuLP 2.9.0 установлен в основном окружении и содержит проверенный CBC. Не заменяйте его произвольно новой основной версией: интерфейс решателя может отличаться. В Streamlit выберите «PuLP / CBC»; в CLI используйте `--backend pulp`. CP-SAT сохранен как другой доступный решатель и остается вариантом по умолчанию.

Воспроизводимые расчеты:

```bash
.venv/bin/python scripts/passport_evidence.py prepare
.venv/bin/python scripts/passport_evidence.py pulp
.venv/bin/python scripts/passport_evidence.py cp_sat
.venv/bin/python scripts/passport_evidence.py milp
.venv/bin/python scripts/passport_evidence.py summarize
```

`prepare` восстанавливает демонстрационный AHP-запрос, OWL и трассировки правил. `milp` экспортирует настоящие LP/MPS в `reports/passport/route_model.*`. Полные запуски используют 100 плановых и 1000 проверочных сценариев; заданный лимит времени не гарантирует побитно одинаковый маршрут. Проверяйте ограничения, статус и диапазон полезности. `summarize` читает сохраненный CP-SAT-результат и обновляет BI-витрины/граф дерева. Полезность потери −0.2 и издержка резерва 0.01 в дереве — явные допущения; вероятность закрытия не измерена.

## База знаний и Protégé

Откройте [travel.owl](../data/knowledge/travel.owl) в **Protégé Desktop 5.6.9** через File → Open. Установщик берите из [официального релиза](https://github.com/protegeproject/protege-distribution/releases/tag/protege-5.6.9); Java должна соответствовать требованиям выбранного дистрибутива. Бинарники Protégé не включены в исходный ZIP.

На вкладке Entities найдите POI, SyntheticPOI и NeedsConfirmationPOI; у SyntheticPOI указан подкласс NeedsConfirmationPOI, у последнего — ограничение `confirmationRequired value true`. Объекты каталога изначально имеют тип SyntheticPOI, но не объявлены NeedsConfirmationPOI. Выберите Reasoner → HermiT → Start reasoner. В DL Query запросите `NeedsConfirmationPOI` с показом Instances: ожидается 173 объекта. Онтология должна быть согласованной; IndoorPOI и OutdoorPOI объявлены непересекающимися.

Python исполняет отдельные [продукционные правила](../data/knowledge/rules.json); OWL-редактор не исполняет JSON-правила. Проверка вывода движком HermiT из распакованного дистрибутива воспроизводится без GUI:

```bash
.venv/bin/python scripts/verify_protege.py --protege-home /path/to/Protege-5.6.9
```

Для этого нужен JDK с возможностью запуска Java source files (11+); при необходимости передайте `--java /path/to/jdk/bin/java`. Скрипт извлекает встроенные зависимости в `.runtime/protege-check`, не скачивает их и проверяет согласованность и число выведенных экземпляров. Результат — `reports/passport/protege_check.json`. При загрузке в настольный Protégé на используемом JDK 21 наблюдалось предупреждение меню AquaLookAndFeel; оно не помешало загрузке OWL. Сам вывод дополнительно проверен headless HermiT.

## Настоящий Apache Superset

Superset 4.1.4 использует отдельное **Python 3.11** окружение: его pandas/numpy/Flask несовместимы с версиями основного приложения. Не устанавливайте BI-lock в `.venv`. Первоначальная установка требует сети, места для второго окружения и Xcode Command Line Tools для сборки `python-geohash==0.8.5`, если подходящего wheel нет. На проверенном Apple Silicon C-сборка прошла; Rust не нужен. Если инструментов нет, владелец Mac устанавливает их командой `xcode-select --install`.

После установки основного приложения:

```bash
python3.11 scripts/bootstrap_superset.py
.venv/bin/python scripts/check_superset.py
.venv/bin/python scripts/run_superset.py
```

Откройте [дашборд](http://127.0.0.1:8088/superset/dashboard/travel-dss/). Логин и случайный пароль создаются в `.runtime/superset/admin.json`; посмотрите файл локально и введите их в форму входа. Имя пользователя — `localadmin`. Пароль, secret-key, metadata.sqlite и BI-кеш в архив не включаются. При новой установке создаются новые учетные данные. Остановка сервера — Ctrl+C.

В дашборде четыре визуализации: сравнение трех планов; ошибки/покрытие прогноза по шести городам; график факта и сезонного прогноза Ярославля за 2025 год; расписание посещений. Таблица `forecast_test` содержит 2190 строк (6×365), `forecast_quality` — 6; размер таблиц маршрутов зависит от экспортированного результата. По умолчанию загружается контрольный CP-SAT/AHP-запуск из паспорта, а не последний пользовательский поиск.

Для своего результата:

```bash
.venv/bin/python scripts/export_bi.py --source reports/my-ahp-pulp.json
.venv/bin/python scripts/setup_superset.py
.venv/bin/python scripts/check_superset.py
```

Обновите страницу дашборда/принудительно обновите графики. Скрипт проверки выполняет вход и реальные REST-запросы Superset к каждому набору данных, проверяет непустой результат и экспорт дашборда. Он не заменяет визуальную приемку интерфейса. Конфигурация — локальный прототип на `127.0.0.1:8088`, SQLite; запуск на публичном сервере этим руководством не предусмотрен. Сервер и копии данных после первоначальной установки работают без сети.
