.PHONY: install run serve lint test eval

install:
	poetry install

run:
	poetry run python agent/main.py

serve:
	poetry run uvicorn agent.api:app --reload --port 8000

lint:
	poetry run ruff check .

test:
	poetry run pytest tests/

eval:
	poetry run python evals/run_evals.py
