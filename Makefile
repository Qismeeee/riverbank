.PHONY: setup db-up db-down kafka-up kafka-down kafka-topics valkey-up valkey-down dev test fmt lint jev synth etl export train-data test-data oracle train-pd notebook eval-report memo-gate llm-eval dossiers

setup:
	cd api && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
	cd api && .venv/bin/pre-commit install --config ../.pre-commit-config.yaml
	@test -f .env || cp .env.example .env
	@echo "xong. Điền .env rồi: make db-up && make dev"

db-up:
	docker compose -f infra/docker-compose.local.yml up -d

db-down:
	docker compose -f infra/docker-compose.local.yml down

KAFKA_TOPICS = docker exec rb-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092
CASE_EVENTS_TOPIC ?= riverbank.case-events

kafka-up:
	docker compose -f infra/docker-compose.local.yml --profile kafka up -d kafka
	$(MAKE) kafka-topics

# Tạo topic trước khi relay chạy, tránh broker tự tạo 1 partition. Thiết kế ở docs/Riverbank-case-events.md
kafka-topics:
	@for i in $$(seq 1 30); do $(KAFKA_TOPICS) --list >/dev/null 2>&1 && break; sleep 1; done
	$(KAFKA_TOPICS) --create --if-not-exists --topic $(CASE_EVENTS_TOPIC) \
		--partitions 6 --replication-factor 1 --config retention.ms=1209600000
	@$(KAFKA_TOPICS) --describe --topic $(CASE_EVENTS_TOPIC) | head -1

kafka-down:
	docker compose -f infra/docker-compose.local.yml --profile kafka stop kafka

valkey-up:
	docker compose -f infra/docker-compose.local.yml --profile valkey up -d valkey

valkey-down:
	docker compose -f infra/docker-compose.local.yml --profile valkey stop valkey

dev:
	cd api && .venv/bin/uvicorn main:app --reload --port 8000

lint:
	cd api && .venv/bin/ruff check . ../eval ../data/synth && .venv/bin/ruff format --check . ../eval ../data/synth

test: lint
	cd api && RIVERBANK_ENV=local .venv/bin/pytest -q

fmt:
	cd api && .venv/bin/ruff format . ../eval ../data/synth && .venv/bin/ruff check --fix . ../eval ../data/synth

jev:
	cd api && .venv/bin/python jev.py

memo-gate:
	cd api && .venv/bin/python ../eval/memo_gate.py $(ARGS)

llm-eval:
	cd api && .venv/bin/python ../eval/llm_eval.py $(ARGS)

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
