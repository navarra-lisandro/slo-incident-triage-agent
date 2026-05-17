# ADR-002: Dependency Management with Poetry 2.3.2

## Status
Accepted

## Context
The project requires a dependency management tool that handles virtual
environments, dependency resolution, and packaging in a reproducible way
across local development, CI, and container builds.

## Decision
I selected Poetry 2.3.2 as the dependency manager. The `package-mode`
is set to `false` in `pyproject.toml` because this project is an
application, not a distributable package.

## Rationale
- Poetry provides deterministic installs via `poetry.lock`
- `pyproject.toml` is the modern Python standard (PEP 517/518)
- `package-mode = false` avoids Poetry 2.x errors when no package
  build target is defined
- Consistent with the previous project, reducing cognitive overhead

## Consequences
- All dependency changes must go through Poetry (`poetry add`)
- CI uses `poetry install --no-root --with dev` to install all
  dependencies including dev group
- `poetry.lock` must be committed and kept in sync with `pyproject.toml`
- Upgrading Poetry major versions requires re-testing the full install chain
