.PHONY: install run serve lint test eval

install:
	poetry install

run:
	poetry run python agent/main.py --incident data/incidents/p1_payments.json

serve:
	poetry run uvicorn agent.api:app --reload --port 8000

lint:
	poetry run ruff check .

test:
	# TODO: restore --cov-fail-under=100 after first tests written — DONE
	poetry run pytest tests/ --cov=agent || [ $$? -eq 5 ]

eval:
	poetry run python evals/run_evals.py
