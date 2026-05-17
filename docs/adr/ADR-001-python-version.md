# ADR-001: Python Version

## Status
Accepted

## Context
The project requires a stable, widely supported Python version that is
compatible with LangChain, LangGraph, LangSmith, and FastAPI. Python
version consistency across local development, CI, and the container
runtime is critical to avoid environment-specific bugs.

## Decision
I selected Python 3.11.9 as the pinned runtime for this project.
The version is declared in `.python-version` (read by pyenv locally),
in `pyproject.toml` (enforced by Poetry), and in the `Dockerfile`
(via `FROM python:3.11.9-slim`).

## Rationale
- Python 3.11 offers significant performance improvements over 3.10
- 3.11.9 is a stable patch release with no known breaking issues
- All core dependencies (LangChain, LangGraph, FastAPI) explicitly
  support 3.11
- Pinning a patch version ensures identical behavior across local
  development, CI, and Kubernetes pod runtime

## Consequences
- All contributors must use pyenv to pin the local Python version
- The Dockerfile must be updated if the Python version is changed
- Dependabot or manual review required if upgrading to 3.12+
