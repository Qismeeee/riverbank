.PHONY: setup db-up db-down dev test fmt lint jev synth etl export train-data test-data oracle train-pd notebook eval-report memo-gate llm-eval dossiers eval-gate test-postgres

PYTHON ?= .venv/bin/python

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
	cd api && $(PYTHON) -m ruff check . ../eval ../data/synth && $(PYTHON) -m ruff format --check . ../eval ../data/synth

test: lint
	cd api && RIVERBANK_ENV=local $(PYTHON) -m pytest -q

test-postgres:
	cd api && $(PYTHON) ../eval/postgres_gate.py

fmt:
	cd api && .venv/bin/ruff format . ../eval ../data/synth && .venv/bin/ruff check --fix . ../eval ../data/synth

jev:
	cd api && .venv/bin/python jev.py

memo-gate:
	cd api && RIVERBANK_ENV=local $(PYTHON) ../eval/memo_gate.py $(ARGS)

eval-gate: memo-gate
	cd api && RIVERBANK_ENV=local $(PYTHON) ../eval/workflow_gate.py

llm-eval:
	cd api && $(PYTHON) ../eval/llm_eval.py $(ARGS)

synth:
	cd api && .venv/bin/python ../data/synth/generate.py --out ../data/synth/out

etl:
	cd api && .venv/bin/python -m etl.run all --run-date $$(date +%F)

export:
	cd api && .venv/bin/python -m etl.export --run-date $$(date +%F)

train-data:
	cd api && .venv/bin/python ../data/synth/train.py --n 20000 --seed 7 --out ../data/synth/train

test-data:
	cd api && .venv/bin/python ../data/synth/train.py --n 5000 --seed 11 --out ../data/synth/test

oracle:
	cd api && .venv/bin/python ../data/synth/oracle.py --n 5000 --seed 11 --out ../data/synth/test/oracle.csv

train-pd:
	cd api && .venv/bin/python ../eval/train_pd.py

notebook:
	cd api && .venv/bin/jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 ../eval/train_pd.ipynb

eval-report:
	cd api && PYTHONPATH=. .venv/bin/python ../eval/score.py

dossiers:
	cd api && .venv/bin/python ../eval/dossiers/make_dossiers.py --date $${DATE:-$$(date +%F)}
