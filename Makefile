PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python

.PHONY: install run test check-contract docker-up docker-down

install:
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -r requirements.txt

run:
	$(PY) -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload

test: check-contract
	$(PY) -m pytest -q

check-contract:
	$(PY) -m fropti_core_contracts.schema --check packages/fropti-core-contracts/schemas/recipe-1.0.0.schema.json

docker-up:
	docker compose up --build

docker-down:
	docker compose down
