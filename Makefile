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
	# TODO: restore coverage when first real test is written
	# poetry run pytest tests/ --cov=agent --cov-fail-under=100
	poetry run pytest tests/ || [ $$? -eq 5 ]

eval:
	poetry run python evals/run_evals.py
