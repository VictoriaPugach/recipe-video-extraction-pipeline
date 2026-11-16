VENV := .venv
export PYTHONUTF8 := 1
# Windows venvs put the interpreter under Scripts\ and name the launcher
# "python"; POSIX venvs use bin/python. $(OS) is Windows_NT on Windows.
ifeq ($(OS),Windows_NT)
PYTHON ?= python
PY := $(VENV)/Scripts/python.exe
else
PYTHON ?= python3
PY := $(VENV)/bin/python
endif

.PHONY: install install-ml run test test-egress check-contract docker-up docker-down

install:
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -r requirements.txt

install-ml: install
	$(PY) -m pip install -r requirements-ml.txt

run:
	$(PY) -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload

test: check-contract
	$(PY) -m pytest -q

test-egress:
	bash scripts/test_egress.sh

check-contract:
	$(PY) -m fropti_core_contracts.schema --check packages/fropti-core-contracts/schemas/recipe-1.0.0.schema.json

docker-up:
	docker compose up --build

docker-down:
	docker compose down
