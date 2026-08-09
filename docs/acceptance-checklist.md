# Чек-лист приёмки

Заполняется на демо, на чистой машине, по инструкции из README, в присутствии
представителя заказчика. Статусы: `✅ выполнено`, `⏳ проверяется на демо`, `❌ не выполнено`.

- **Дата проверки:** _(заполнить)_
- **Конфигурация стенда:** _(vCPU / RAM / диск / ОС / версия Docker)_
- **Профиль данных:** `almaty`
- **Коммит:** _(заполнить)_

---

## 1. Матрица приёмки (раздел 15.1 ТЗ)

| ID | Критерий | Способ проверки | Уровень | Статус | Комментарий |
|---|---|---|---|---|---|
| AC-01 | Развёртывание воспроизводится по README на чистой машине | clone → `cp .env.example .env` → `docker compose up -d` → `/ready` = 200 | MUST | ⏳ | Проверяется на демо |
| AC-02 | API отвечает на все три режима корректными полигонами | `./scripts/smoke_test.sh` → код 0 | MUST | ⏳ | Скрипт готов, прогон на демо |
| AC-03 | Мультиконтуры 10/20/30 одним запросом, три Feature | запрос из README | MUST | ⏳ | Автотест `test_successful_response_shape` |
| AC-04 | Диапазон 5–60 работает на границах, 4 и 61 отклоняются с 400 | ручные запросы | MUST | ✅ | `test_boundary_contours_are_accepted`, `test_invalid_contours_are_rejected_with_400` |
| AC-05 | Ответ — валидный GeoJSON, открывается в QGIS | загрузка файла в QGIS | MUST | ⏳ | Скачать через «Скачать GeoJSON» на демо-странице |
| AC-06 | Инварианты GEO-01…GEO-08 выполняются | отчёт pytest | MUST | ✅ | `tests/unit/test_geo_invariants.py`, на живых данных — `tests/integration` |
| AC-07 | Полигоны не выходят за оз. Сайран и не пересекают Б. Алматинку вне мостов | визуальный осмотр, точки 2 и 3 | MUST | ⏳ | Требует живых данных |
| AC-08 | Изохрона не уходит в горный массив южнее города | контрольная точка 4 | MUST | ⏳ | Требует живых данных |
| AC-09 | Время ответа соответствует нормативам 10.2 | отчёт k6 (p50/p95/p99) | MUST | ⏳ | `make load-test` |
| AC-10 | Нероутируемая точка и точка вне покрытия → корректные 422 | точки 6 и 7 | MUST | ⏳ | Автотесты для 422 есть, точка 6 зависит от данных |
| AC-11 | Сервис не падает при 10 RPS × 5 мин, 5xx < 0.5 % | отчёт нагрузочного теста | MUST | ⏳ | `make load-test`, пороги зашиты в сценарий |
| AC-12 | Остановка Redis не приводит к отказу API и не нарушает нормативы 10.2 | `docker compose stop redis` → прогон k6 → 200 и p95 в норме, **совместно с AC-09 одним прогоном** | MUST | ⏳ | Деградация, короткий connect-таймаут и circuit breaker покрыты unit-тестами `test_cache_degrades_without_a_server`, `test_cache_breaker_*` |
| AC-13 | Swagger UI открывается, модели описаны, «Try it out» работает | демонстрация | MUST | ✅ | `test_swagger_ui_is_served`, `test_no_schema_is_left_undocumented` |
| AC-14 | Клик → маркер → построение → отрисовка → скачивание GeoJSON | демонстрация | MUST | ⏳ | Проверяется на демо |
| AC-15 | Легенда, площади и время ответа на странице | демонстрация | MUST | ⏳ | Реализовано в `web/app.js` |
| AC-16 | `docker compose down && up` не пересобирает тайлы | демонстрация | MUST | ⏳ | Идемпотентность через `data_version` |
| AC-17 | Все образы зафиксированы по тегу, `latest` отсутствует | ревью compose | MUST | ✅ | Проверяется job `compose` в CI |
| AC-18 | Наружу опубликован только порт `web` | автопроверка: шаг 7 `scripts/smoke_test.sh` и job `compose` в CI | MUST | ✅ | Dev-профиль вынесен в `docker-compose.dev.yml`, автоматически не подхватывается |
| AC-19 | Логи структурированы, содержат `request_id` и `duration_ms` | `docker compose logs api` | MUST | ⏳ | structlog JSON, поля из п. 10.5 |
| AC-20 | Раздел «Известные ограничения» заполнен содержательно | ревью | MUST | ✅ | README, раздел 10 |
| AC-21 | CI зелёный, покрытие бизнес-логики ≥ 70 % | отчёт CI | MUST | ✅ | Порог зашит в `--cov-fail-under=70`, фактически ~90 % |
| AC-22 | Permalink восстанавливает состояние демо-страницы | демонстрация | SHOULD | ⏳ | Состояние в `location.hash` |
| AC-23 | `/metrics` отдаёт метрики Prometheus | `curl` | SHOULD | ✅ | `test_metrics_are_exposed_in_prometheus_format` |
| AC-24 | Работоспособность на macOS arm64 | демонстрация или протокол | SHOULD | ⏳ | Образ Valhalla имеет arm64-манифест, требуется прогон |
| AC-25 | Во время первичной сборки тайлов запрос через `web` → `503 ENGINE_UNAVAILABLE` с текстом о подготовке данных, не 502/500/таймаут | `docker volume rm` → `docker compose up -d` → немедленный `curl` через 8080 | MUST | ⏳ | `EngineMonitor` различает `preparing` и `unavailable`; unit-тесты `test_monitor_*`, `test_isochrone_returns_503_without_calling_an_engine_that_is_not_ready` |

## 2. Геометрические инварианты (раздел 13.2 ТЗ)

| ID | Инвариант | Тест | Статус |
|---|---|---|---|
| GEO-01 | Все геометрии валидны | `test_geo_01_every_returned_geometry_is_valid` | ✅ |
| GEO-02 | Площадь растёт с временем | `test_geo_02_area_grows_monotonically_with_time` | ✅ |
| GEO-03 | Меньший контур вложен в больший (допуск 1 %) | `test_geo_03_smaller_contours_are_contained_in_larger_ones` | ✅ |
| GEO-04 | Snapped origin внутри наименьшего контура | `test_geo_04_snapped_origin_lies_inside_the_smallest_contour` | ✅ |
| GEO-05 | Кольца не пересекаются | `test_geo_05_rings_do_not_overlap` | ✅ |
| GEO-06 | `area(auto) > area(bicycle) > area(pedestrian)` | `test_geo_06_auto_reaches_further_than_bicycle_and_pedestrian` | ✅ |
| GEO-07 | Пересечение с крупным водоёмом нулевое | `test_geo_07_water_is_cut_out_of_the_result` | ✅ |
| GEO-08 | Cache hit возвращает идентичную геометрию | `test_geo_08_cache_hit_returns_identical_geometry` | ✅ |

Инварианты проверяются в двух контурах: на синтетическом движке (unit, каждый прогон CI) и
на живых данных Алматы (`tests/integration`, требуется поднятый стек).

## 3. Функциональные требования (раздел 5 ТЗ)

| ID | Уровень | Где реализовано | Статус |
|---|---|---|---|
| FR-01 | MUST | `IsochroneRequest.lat/lon` | ✅ |
| FR-02 | MUST | `ContourMinutes`, `MIN/MAX_CONTOUR_MINUTES` | ✅ |
| FR-03 | MUST | `MAX_CONTOURS=4` | ✅ |
| FR-04 | MUST | `TravelMode`, `COSTING_BY_MODE` | ✅ |
| FR-05 | MUST | `IsochroneFeatureCollection` | ✅ |
| FR-06 | MUST | `_build_features`, порядок по убыванию времени | ✅ |
| FR-07 | MUST | `build_rings`, `options.rings` | ✅ |
| FR-08 | MUST | `WaterIndex.subtract`, `scripts/prepare_water.sh` | ✅ |
| FR-09 | MUST | `area_perimeter_km` (pyproj.Geod) | ✅ |
| FR-10 | MUST | `metadata` + дублирование в `properties` | ✅ |
| FR-11 | MUST | `CacheService`, `CACHE_TTL_SECONDS` | ✅ |
| FR-12 | MUST | `web/index.html`, `web/app.js` | ✅ |
| FR-13 | MUST | кнопка «Скачать GeoJSON» | ✅ |
| FR-14 | MUST | панель результата: площади, `duration_ms`, cache, снап | ✅ |
| FR-15 | MUST | `/api/v1/health`, `/api/v1/ready` | ✅ |
| FR-16 | SHOULD | `/api/v1/meta` | ✅ |
| FR-17 | SHOULD | `GET /api/v1/isochrone` | ✅ |
| FR-18 | SHOULD | permalink в `location.hash` | ✅ |
| FR-19 | SHOULD | `/metrics` | ✅ |
| FR-20 | SHOULD | буферизация `waterway=river/stream` (+ `canal`) | ✅ |
| FR-21 | MAY | `options.departure_time` → `date_time.type=1` | ✅ |
| FR-22 | MAY | TopoJSON / WKT | ❌ не реализовано (уровень MAY) |

## 4. Нормативы производительности (раздел 10.2 ТЗ)

Заполняется по отчёту k6 на эталонной конфигурации, профиль `almaty`.

| Сценарий | Норматив p50 | Норматив p95 | Жёсткий лимит | Факт p50 | Факт p95 | Факт p99 | Статус |
|---|---|---|---|---|---|---|---|
| `pedestrian` 10/20/30 | ≤ 0.6 с | ≤ 1.2 с | 3 с | | | | ⏳ |
| `bicycle` 10/20/30 | ≤ 0.8 с | ≤ 1.5 с | 3 с | | | | ⏳ |
| `auto` 10/20/30 | ≤ 1.2 с | ≤ 2.2 с | 3 с | | | | ⏳ |
| `auto` один контур 60 мин | ≤ 2.0 с | ≤ 2.8 с | 3 с | | | | ⏳ |
| Любой сценарий, cache hit | ≤ 50 мс | ≤ 120 мс | 200 мс | | | | ⏳ |
| Нагрузка 10 RPS × 5 мин | — | p95 ≤ 3 с | 5xx < 0.5 % | | | | ⏳ |

## 5. Definition of Done (раздел 15.2 ТЗ)

- [ ] Код в основной ветке, история осмысленная, без коммитов `fix` / `wip` / `123`
- [ ] Секреты и большие бинарные артефакты в репозиторий не попали (`.env` в `.gitignore`,
      `data/` игнорируется)
- [ ] CI зелёный на последнем коммите
- [ ] README проверен «вслепую» третьим лицом
- [ ] Демо проведено, все AC уровня MUST подтверждены
- [ ] Открытые вопросы и известные баги оформлены задачами в трекере
- [ ] Таблица времени первичной сборки тайлов в README заполнена фактическими замерами
- [ ] `docs/screenshot.png` снят с работающего стенда

## 6. Открытые вопросы к заказчику (раздел 19 ТЗ)

| # | Вопрос | Текущее допущение исполнителя | Ответ |
|---|---|---|---|
| 1 | Алматы или весь Казахстан по умолчанию? | по умолчанию `almaty`, остальные профили переключаются переменной | |
| 2 | Нужно ли развёртывание на сервере заказчика? | только локальное демо | |
| 3 | Предпочтения по языку API-слоя? | Python 3.12 + FastAPI (основной вариант ТЗ) | |
| 4 | Режим `rings` включён по умолчанию? | выключен, `RINGS_DEFAULT=false` | |
| 5 | Планируется ли публичный доступ к API? | заложены точки расширения под аутентификацию, сама она вне объёма | |
| 6 | Кто и как подтверждает «визуальную корректность» (AC-07, AC-08)? | скриншоты контрольных точек 2–4 согласуются на демо | |
