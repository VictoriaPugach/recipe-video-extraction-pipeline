PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python

.PHONY: install run test docker-up docker-down

install:
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -r requirements.txt

run:
	$(PY) -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload

test:
	$(PY) -m pytest -q

docker-up:
	docker compose up --build

docker-down:
	docker compose down
