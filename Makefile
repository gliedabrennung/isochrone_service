SHELL := /bin/bash
COMPOSE := docker compose
COMPOSE_PROD := docker compose -f docker-compose.yml
COMPOSE_DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml
COMPOSE_DEBUG := docker compose -f docker-compose.yml -f docker-compose.debug.yml
WEB_PORT ?= 8080
BASE_URL ?= http://localhost:$(WEB_PORT)
VENV_PY := $(CURDIR)/api/.venv/bin/python
PY := $(shell test -x $(CURDIR)/api/.venv/bin/python && echo $(CURDIR)/api/.venv/bin/python || echo python3)

.DEFAULT_GOAL := help
.PHONY: help up down dev debug restart logs logs-valhalla ps build \
        smoke test test-unit test-integration test-contract cov lint fmt fmt-check \
        openapi openapi-check rebuild-tiles update-data load-test load-test-degraded \
        clean venv

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env

up: .env ## Поднять стек (prod-профиль, наружу только web)
	$(COMPOSE_PROD) up -d --build

down: ## Остановить стек, тайлы и данные сохраняются в volume
	$(COMPOSE_PROD) down

dev: .env ## Поднять стек в dev-режиме (hot-reload, проброшенные порты)
	$(COMPOSE_DEV) up -d --build

debug: .env ## Поднять стек с отладочными портами на 127.0.0.1
	$(COMPOSE_DEBUG) up -d --build

restart: ## Перезапустить api
	$(COMPOSE_PROD) restart api

ps: ## Список сервисов и опубликованных портов
	$(COMPOSE_PROD) ps

build: ## Пересобрать образы
	$(COMPOSE_PROD) build

logs: ## Логи api
	$(COMPOSE_PROD) logs -f api

logs-valhalla: ## Логи сборки тайлов
	$(COMPOSE_PROD) logs -f valhalla

smoke: ## Smoke-тест поднятого стека
	BASE_URL=$(BASE_URL) ./scripts/smoke_test.sh

test: test-unit ## Синоним test-unit

test-unit: ## Unit-тесты с отчётом покрытия
	$(PY) -m pytest tests/unit -q \
		--cov=app --cov-report=term-missing --cov-fail-under=70

test-integration: ## Интеграционные тесты против поднятого стека
	RUN_INTEGRATION_TESTS=1 BASE_URL=$(BASE_URL) $(PY) -m pytest tests/integration -q

test-contract: ## Contract-тесты (schemathesis по OpenAPI)
	$(PY) -m pytest tests/contract -q

cov: ## HTML-отчёт покрытия
	$(PY) -m pytest tests/unit -q --cov=app --cov-report=html

lint: ## ruff check
	$(PY) -m ruff check .

fmt: ## ruff format
	$(PY) -m ruff format .

fmt-check: ## ruff format --check
	$(PY) -m ruff format --check .

openapi: ## Выгрузить docs/openapi.yaml из приложения
	$(PY) scripts/export_openapi.py docs/openapi.yaml

openapi-check: ## Проверить, что docs/openapi.yaml актуален
	$(PY) scripts/export_openapi.py --check docs/openapi.yaml

rebuild-tiles: ## Полная пересборка тайлов Valhalla из уже скачанного PBF
	$(COMPOSE_PROD) down
	docker volume rm $${COMPOSE_PROJECT_NAME:-isochrone}_valhalla_tiles
	$(COMPOSE_PROD) up -d --build

update-data: ## Скачать свежий PBF и пересобрать тайлы и слой воды
	$(COMPOSE_PROD) down
	docker volume rm $${COMPOSE_PROJECT_NAME:-isochrone}_valhalla_tiles \
		$${COMPOSE_PROJECT_NAME:-isochrone}_osm_source \
		$${COMPOSE_PROJECT_NAME:-isochrone}_app_data
	OSM_FORCE_REFRESH=true $(COMPOSE_PROD) up -d --build

load-test: ## Нагрузочный тест k6: 10 RPS x 5 мин
	docker run --rm --network host \
		-e BASE_URL=$(BASE_URL) \
		-v "$(CURDIR)/load:/load" \
		grafana/k6:0.53.0 run /load/k6-scenario.js

load-test-degraded: ## AC-12 + AC-09 одним прогоном: тот же k6 при остановленном Redis
	$(COMPOSE_PROD) stop redis
	@status=0; $(MAKE) load-test || status=$$?; $(COMPOSE_PROD) start redis; exit $$status

clean: ## Удалить контейнеры и все volume (данные будут скачаны заново)
	$(COMPOSE_PROD) down -v

venv: ## Локальное окружение для тестов и линта (Python 3.12)
	uv venv --python 3.12 api/.venv
	uv pip install --python api/.venv/bin/python -e "api[dev]"
