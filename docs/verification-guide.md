# Как проверить, что сервис работает

Пошаговый сценарий проверки: от «собирается ли код» до «выдерживает ли нагрузку и отказ
зависимостей». Каждый шаг — команда, ожидаемый результат и ссылка на критерий приёмки из
раздела 15.1 ТЗ.

Проверки разбиты на четыре уровня. Уровень 1 не требует поднятого стека и занимает меньше
минуты. Уровни 2–4 требуют работающего `docker compose` и данных Алматы.

Заполненный чек-лист приёмки — [acceptance-checklist.md](acceptance-checklist.md); там же
лежат эталонные цифры, с которыми стоит сравнивать свои замеры.

---

## Что нужно

- Docker 24+ с Compose v2, права на запуск контейнеров.
- Для уровня 1 — Python 3.12 и `make venv` (создаёт `api/.venv`).
- Свободные 4 ГБ на диске и 4 ГБ RAM для профиля `almaty`.
- Для нагрузочных прогонов — доступ к образу `grafana/k6:0.53.0`.

Все команды выполняются из корня репозитория.

---

## Уровень 0. Проверка за две минуты

Если нужно просто убедиться, что всё живо:

```bash
cp .env.example .env
docker compose up -d
make smoke
```

`smoke_test.sh` сам дожидается готовности движка (до 60 с) и печатает итог. Ожидаемый
вывод — 10 строк `PASS` и `passed: 10, failed: 0`; код возврата 0. Любой `FAIL` печатает
тело ответа, по которому видно, что именно сломалось.

При первом запуске на чистой машине сначала скачивается PBF и собираются тайлы — смотрите
[раздел 3 README](../README.md#3-первичная-сборка-сколько-ждать-и-как-следить).

---

## Уровень 1. Без поднятого стека

Проверяет код и контракты, не требует данных и контейнеров.

```bash
make venv          # один раз: окружение Python 3.12 в api/.venv
make lint          # ruff check
make fmt-check     # ruff format --check
make test-unit     # unit-тесты + порог покрытия
make test-contract # schemathesis по сгенерированной OpenAPI-схеме
make openapi-check # docs/openapi.yaml не разошёлся с приложением
```

Что должно получиться:

| Команда | Ожидаемый результат | Критерий |
|---|---|---|
| `make lint` | `All checks passed!` | AC-21 |
| `make fmt-check` | `… files already formatted`, ни одного к переформатированию | AC-21 |
| `make test-unit` | все тесты зелёные, покрытие ≥ 70 % (на 2026-08-10 — 207 тестов, 91 %) | AC-04, AC-06, AC-21 |
| `make test-contract` | все тесты зелёные (на 2026-08-10 — 9) | AC-13 |
| `make openapi-check` | `docs/openapi.yaml is up to date` | п. 14.2 ТЗ |

Unit-тесты гоняют геометрические инварианты GEO-01…GEO-08 на синтетическом движке, поэтому
проходят без данных. Те же инварианты на живых данных проверяет уровень 2.

Если `make test-unit` падает по покрытию, а не по тестам — смотрите, какие строки
непокрыты, в колонке `Missing` отчёта.

---

## Уровень 2. Живой стек

### 2.1 Автоматические проверки

```bash
docker compose up -d
make smoke              # 10 проверок, код возврата 0
make test-integration   # тесты против поднятого стека
```

`make test-integration` требует поднятого стека и переменной `RUN_INTEGRATION_TESTS=1` —
Makefile выставляет её сам. Если запустить `pytest tests/integration` напрямую, без этой
переменной, тесты не упадут, а пропустятся: «20 skipped» означает, что переменная не
выставлена, а не что всё в порядке.

Что закрывают эти два прогона:

| Проверка | Критерий |
|---|---|
| Три режима передвижения возвращают полигоны | AC-02 |
| Мультиконтуры 10/20/30 одним запросом, три `Feature` | AC-03 |
| Границы 5 и 60 принимаются, 4 и 61 отклоняются с 400 | AC-04 |
| GEO-01…GEO-08 на живых данных Алматы | AC-06 |
| Оз. Сайран вырезано из результата | AC-07 |
| `auto` 60 мин от Медеу не захватывает горный гребень | AC-08 |
| Точка вне bbox и нероутируемая точка возвращают 422 | AC-10 |
| Наружу опубликован только порт `web` | AC-18 |

### 2.2 Ручные запросы

Служебные эндпоинты:

```bash
curl -s localhost:8080/api/v1/health          # {"status":"ok","version":"1.0.0"}
curl -s localhost:8080/api/v1/ready           # status: ok, все компоненты ok
curl -s localhost:8080/api/v1/meta            # bbox, срез OSM, режимы, лимиты
curl -s localhost:8080/metrics | grep -c '^isochrone_'   # > 0
```

Основной расчёт:

```bash
curl -s -X POST localhost:8080/api/v1/isochrone \
  -H 'Content-Type: application/json' \
  -d '{"lat":43.238949,"lon":76.889709,"contours":[10,20,30],"mode":"pedestrian"}' \
  | python3 -m json.tool | head -40
```

На что смотреть в ответе:

- `Content-Type: application/geo+json`, заголовки `X-Request-Id` и `X-Cache`;
- три `Feature`, порядок `contour_minutes` — 30, 20, 10 (от большего к меньшему);
- в `properties` каждого `Feature` есть `area_km2`, `perimeter_km`, `color`, а также
  продублированные метаданные — это требование п. 6.2 ТЗ для совместимости с ГИС;
- в `metadata` есть `snapped_origin`, `snap_distance_m`, `engine_version`,
  `osm_data_timestamp`, `duration_ms`, `engine_ms`, `cache`.

Повторите тот же запрос — второй ответ должен прийти с `X-Cache: HIT` и `duration_ms`
меньше 50 мс, геометрия при этом побайтно та же (GEO-08).

Негативные сценарии:

```bash
# вне покрытия -> 422 POINT_OUT_OF_COVERAGE
curl -s -X POST localhost:8080/api/v1/isochrone -H 'Content-Type: application/json' \
  -d '{"lat":51.1605,"lon":71.4704,"contours":[10],"mode":"auto"}'

# нероутируемая точка в горах -> 422 POINT_NOT_ROUTABLE, снап ~2073 м
curl -s -X POST localhost:8080/api/v1/isochrone -H 'Content-Type: application/json' \
  -d '{"lat":43.06,"lon":77.05,"contours":[10],"mode":"pedestrian"}'

# невалидные параметры -> 400 VALIDATION_ERROR
curl -s -X POST localhost:8080/api/v1/isochrone -H 'Content-Type: application/json' \
  -d '{"lat":43.2389,"lon":76.8897,"contours":[61],"mode":"auto"}'
```

Все три обязаны прийти с `Content-Type: application/problem+json` и полями `type`, `title`,
`status`, `detail`, `instance`, `request_id`, `code` — формат RFC 7807 (п. 6.5 ТЗ).

### 2.3 Логи

```bash
docker compose logs api | grep http_request | tail -3
```

Каждая запись — одна строка JSON с полями `request_id`, `method`, `path`, `status`,
`duration_ms` и, для расчётов, `mode`, `contours`, `lat`, `lon`. Это AC-19.

### 2.4 Swagger UI

Откройте <http://localhost:8080/api/v1/docs>. Проверьте, что модели раскрываются с
описаниями и примерами, а кнопка «Try it out» на `POST /api/v1/isochrone` возвращает 200.
ReDoc — на `/api/v1/redoc`. Это AC-13.

---

## Уровень 3. Нагрузка

```bash
make load-test            # 10 RPS x 5 мин, штатный профиль
make load-test-degraded   # тот же прогон при остановленном Redis
```

`load-test-degraded` сам останавливает и возвращает Redis. Именно так требует проверять
AC-12 раздел 15.1 ТЗ: изолированная проверка «вернулся 200» пропускает деградацию по
времени, поэтому AC-12 подтверждается вместе с AC-09 одним прогоном.

Эталонные цифры со стенда AMD Ryzen 5 3500U / 13 ГБ / NVMe (замер 2026-08-10):

| Прогон | p50 | p95 | p99 | 5xx |
|---|---|---|---|---|
| Штатный | 5 мс | 337 мс | 724 мс | 0 |
| Redis остановлен | 115 мс | 1090 мс | 1903 мс | 0 |

Пороги зашиты в сам сценарий (`http_req_duration p(95)<3000`, `http_req_failed rate<0.005`,
`server_errors count<1`), поэтому k6 сам завершится ненулевым кодом при нарушении. Это
AC-09, AC-11 и AC-12.

Отдельно стоит смотреть на `cache_hit_rate` в итоговой сводке. Штатный сценарий ходит по
сетке из 18 точек и даёт около 83 % попаданий в кэш — это реалистично для продуктового
трафика, но по такому прогону нельзя судить о поведении на холодном кэше. Чтобы получить
худший случай, поднимите долю уникальных точек, например увеличив `OFFSET_STEPS` в
`load/k6-scenario.js`. На эталонном стенде холодный прогон даёт p95 1046 мс и p99 1842 мс
при нулевом числе 5xx.

Замер одиночных запросов из таблицы п. 10.2 ТЗ нагрузочный сценарий не заменяет: там
требуется отсутствие конкурентной нагрузки и гарантированный cache miss. Достаточно
последовательно послать по 30 запросов на сценарий, каждый раз сдвигая координату на
0.001° и проверяя, что пришёл `X-Cache: MISS`.

---

## Уровень 4. Отказы зависимостей

### 4.1 Остановленный Redis

```bash
docker compose stop redis
curl -s localhost:8080/api/v1/ready
curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' -X POST \
  localhost:8080/api/v1/isochrone -H 'Content-Type: application/json' \
  -d '{"lat":43.2515,"lon":76.9012,"contours":[15],"mode":"pedestrian"}'
docker compose start redis
```

Ожидается: `/ready` отдаёт **200** с телом `status: degraded` и `cache: unavailable` —
кэш не является обязательной зависимостью (п. 6.4 ТЗ), поэтому оркестратор продолжает
слать сюда трафик. Расчёт возвращает 200 и укладывается в норматив: короткий
`REDIS_CONNECT_TIMEOUT_MS` и circuit breaker не дают запросам платить за мёртвый кэш.

В логах при этом должно быть **одно** предупреждение `cache_unavailable` на переход
состояния, а не по одному на каждый запрос (п. 10.3 ТЗ):

```bash
docker compose logs api | grep -c cache_unavailable
```

Записи уровня `error` с текстом `Future exception was never retrieved: gaierror` —
известный шум asyncio: клиент Redis отменяет соединение по таймауту, а резолв имени
завершается позже. На ответы они не влияют.

### 4.2 Остановленный движок

```bash
docker compose stop valhalla
sleep 20
curl -s localhost:8080/api/v1/ready
curl -s -X POST localhost:8080/api/v1/isochrone -H 'Content-Type: application/json' \
  -d '{"lat":43.2711,"lon":76.9133,"contours":[10],"mode":"bicycle"}'
docker compose start valhalla
```

Ожидается: `/ready` отдаёт **503** со `status: unavailable` и подсказкой
`Проверьте состояние контейнера: docker compose ps valhalla`. Расчёт на новых координатах
возвращает `503 ENGINE_UNAVAILABLE` **немедленно**, а не через `ENGINE_TIMEOUT_S` —
состояние движка держит фоновый поллер `EngineMonitor`.

Пауза в 20 секунд нужна поллеру: движок объявляется недоступным только после
`ENGINE_FAILURE_THRESHOLD` неудачных проверок подряд с интервалом
`ENGINE_POLL_INTERVAL_S`, чтобы единичный сбой под нагрузкой не выключал сервис.

Отдельно любопытный и корректный эффект: запрос, который уже лежит в кэше, при выключенном
движке всё равно вернёт 200. Кэш обслуживает трафик, пока движок недоступен.

### 4.3 Первичная сборка тайлов (AC-25)

Разрушающая проверка: тайлы удаляются и собираются заново. Исходный PBF лежит в отдельном
volume `osm_source` и повторно не скачивается.

```bash
docker compose down
docker volume rm isochrone_valhalla_tiles
docker compose up -d
curl -s -X POST localhost:8080/api/v1/isochrone -H 'Content-Type: application/json' \
  -d '{"lat":43.2389,"lon":76.8897,"contours":[10],"mode":"pedestrian"}'
```

Ожидается `503 ENGINE_UNAVAILABLE` с `detail`, который говорит именно о подготовке данных:
«Идёт первичная подготовка данных и сборка тайлов Valhalla для профиля 'almaty'…».
Ни 502, ни 500, ни таймаут — в этом и смысл критерия: на демо это разница между «работает,
ждём» и «сломано».

На эталонном стенде подготовка данных занимает 10 с, сборка тайлов — 45 с, движок готов
через 60 с от `up`; контейнер `api` принимает запросы уже через 14 с.

### 4.4 Перезапуск без пересборки тайлов (AC-16)

```bash
docker compose down && docker compose up -d
docker compose logs valhalla | grep -i "no need to rebuild"
```

Ожидается строка `INFO: Routing tiles exist and no need to rebuild.` и `/ready` = 200
примерно через 10 секунд. Тайлы живут в именованном volume и переживают `down`.

---

## Веб-страница

Проверяется руками на <http://localhost:8080>.

| Что сделать | Что должно произойти | Критерий |
|---|---|---|
| Открыть страницу | Карта Алматы, зум 12, в углу атрибуция «© OpenStreetMap contributors» | п. 7.1 ТЗ |
| Убедиться, что подложка загрузилась | Видны улицы и подписи, а не серый фон | п. 7.1 ТЗ |
| Кликнуть по карте | Ставится перетаскиваемый маркер, координаты попадают в поля `lat`/`lon` | AC-14 |
| Выбрать режим и контуры, нажать «Построить» | Кнопка блокируется на время запроса, затем рисуются полигоны с заливкой и обводкой | AC-14 |
| Посмотреть на панель результата | Площадь каждого контура в км², `duration_ms`, `cache: hit/miss`, дистанция снапа | AC-15 |
| Посмотреть на легенду | Цвета контуров с подписями «≤ N мин» | AC-15 |
| Нажать «Скачать GeoJSON» | Скачивается файл; открывается в QGIS как валидный слой | AC-05, AC-14 |
| Включить `rings` | Контуры перерисовываются непересекающимися кольцами | FR-07 |
| Скопировать URL, открыть в новой вкладке | Состояние карты и параметров восстановлено из хеша | AC-22 |
| Запросить точку вне покрытия | Показан читаемый текст ошибки (`title` и `detail`), а не `alert('Error')` | п. 7.1 ТЗ |

Для AC-05 файл нужно именно открыть в QGIS или другом ГИС-редакторе: это проверяет не
только синтаксис, но и то, что порядок координат `[lon, lat]` и типы геометрии
(`Polygon` / `MultiPolygon`) поняты сторонним инструментом.

Если вместо карты серый фон, начните с конфигурации подложки — её рендерит контейнер `web`
при старте:

```bash
curl -s localhost:8080/config.js
```

`basemapTileUrl` обязан быть шаблоном ровно с тремя плейсхолдерами — `{z}`, `{x}` и `{y}`.
Лишние или обрезанные фигурные скобки означают, что URL испорчен при подстановке
переменных окружения, и MapLibre молча не загрузит ни одного тайла: карта инициализируется,
но остаётся пустой. Это же проверяет автотест
`test_web_config_renders_a_usable_basemap_template`. Если шаблон корректен, проверьте, что
`https://tile.openstreetmap.org` доступен из браузера — публичные тайлы OSM могут быть
заблокированы сетью или tile usage policy.

---

## Сводная таблица: критерий приёмки → команда

| Критерий | Чем проверяется |
|---|---|
| AC-01 | `cp .env.example .env && docker compose up -d`, затем `/ready` = 200 |
| AC-02, AC-18 | `make smoke` |
| AC-03, AC-04, AC-06, AC-07, AC-08, AC-10 | `make test-integration` |
| AC-05 | «Скачать GeoJSON» на демо-странице, открыть в QGIS |
| AC-09, AC-11 | `make load-test` |
| AC-12 | `make load-test-degraded` |
| AC-13 | `make test-contract`, затем Swagger UI вручную |
| AC-14, AC-15, AC-22 | демо-страница вручную, таблица выше |
| AC-16 | `docker compose down && up`, грепнуть «no need to rebuild» |
| AC-17 | `docker compose config`, убедиться в отсутствии тега `latest` |
| AC-19 | `docker compose logs api \| grep http_request` |
| AC-21 | `make lint`, `make fmt-check`, `make test-unit` |
| AC-23 | `curl localhost:8080/metrics` |
| AC-25 | `docker volume rm isochrone_valhalla_tiles`, затем немедленный запрос |

---

## Если что-то не сходится

- `/ready` долго отдаёт 503 — идёт сборка тайлов, смотрите `docker compose logs -f valhalla`.
- Расчёт возвращает 503 при поднятом `valhalla` — проверьте `docker compose logs api` на
  `engine_state_changed` и `engine_transport_error`.
- Расчёт возвращает 504 — движок не уложился в `ENGINE_TIMEOUT_S`; на профилях крупнее
  `almaty` это ожидаемо для `auto 60`, см. раздел «Известные ограничения» в README.
- Интеграционные тесты «прошли» подозрительно быстро — вероятно, все ответы пришли из кэша.
  `docker compose restart redis` очищает его: в конфигурации по умолчанию Redis запущен с
  `--save ""` и `--appendonly no`, то есть без персистентности.
- Нагрузочный прогон не запускается — образ `grafana/k6:0.53.0` тянется из Docker Hub,
  проверьте сеть.

Остальные типовые проблемы — [раздел 11 README](../README.md#11-troubleshooting).
