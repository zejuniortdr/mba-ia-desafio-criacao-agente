PORT ?= 8000
RUFF := uvx ruff@0.17.0

.PHONY: help install run restaurar test e2e lint format typecheck check openapi

help: ## Lista os comandos
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Instala dependências e cria o .env a partir do .env.example, se faltar
	uv sync
	@test -f .env || (cp .env.example .env && echo "criado .env: preencha GOOGLE_API_KEY")

run: ## Sobe a API na porta 8000 (outra porta: make run PORT=8010)
	uv run --env-file .env uvicorn --factory aurora.api:criar_api --port $(PORT)

restaurar: ## Volta reservas e visitantes ao estado de dados/ e apaga as sessões (pare a API antes)
	uv run python -m aurora.db restaurar

test: ## Suíte sem LLM (modelo falso), não gasta cota
	uv run pytest

e2e: ## Fluxo do avaliador com o Gemini real: GASTA COTA (~32 chamadas)
	uv run --env-file .env pytest -m e2e -s

lint: ## ruff check e format --check, como no CI
	$(RUFF) check aurora tests
	$(RUFF) format --check aurora tests

format: ## Formata o código com ruff
	$(RUFF) format aurora tests

typecheck: ## mypy, como no CI
	uv run --with mypy==2.4.0 mypy aurora --ignore-missing-imports

check: lint typecheck test ## Tudo que o CI roda

openapi: ## Exporta a especificação OpenAPI para docs/openapi.json (rode após mudar rotas)
	uv run python -c "import json; from aurora.api import criar_api; print(json.dumps(criar_api().openapi(), ensure_ascii=False, indent=2))" > docs/openapi.json
