.PHONY: setup db-up db-down dev test fmt lint jev synth etl export

setup:
	cd api && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
	cd api && .venv/bin/pre-commit install --config ../.pre-commit-config.yaml
	@test -f .env || cp .env.example .env
	@echo "xong. Điền .env rồi: make db-up && make dev"

db-up:
	docker compose -f infra/docker-compose.local.yml up -d

db-down:
	docker compose -f infra/docker-compose.local.yml down

dev:
	cd api && .venv/bin/uvicorn main:app --reload --port 8000

lint:
	cd api && .venv/bin/ruff check . && .venv/bin/ruff format --check .

test: lint
	cd api && RIVERBANK_ENV=local .venv/bin/pytest -q

fmt:
	cd api && .venv/bin/ruff format . && .venv/bin/ruff check --fix .

jev:
	cd api && .venv/bin/python jev.py

synth:
	cd api && .venv/bin/python ../data/synth/generate.py --out ../data/synth/out

etl:
	cd api && .venv/bin/python -m etl.run all --run-date $$(date +%F)

export:
	cd api && .venv/bin/python -m etl.export --run-date $$(date +%F)
