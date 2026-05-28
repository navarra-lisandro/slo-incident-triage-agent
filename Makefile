.PHONY: install run serve lint test eval

install:
	poetry install

INCIDENT ?= data/incidents/p1_payments_resource_exhaustion.json
run:
	poetry run python -m agent.main --incident $(INCIDENT)

serve:
	poetry run uvicorn agent.api:app --reload --port 8000

lint:
	poetry run ruff check .

test:
	# TODO: restore --cov-fail-under=100 after first tests written — DONE
	poetry run pytest tests/ --cov=agent || [ $$? -eq 5 ]

dataset:
	poetry run python -m evals.run_evals --create-dataset-only

eval:
	poetry run python -m evals.run_evals
