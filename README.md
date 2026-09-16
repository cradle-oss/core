# CORE

CORE is a terminal-native developer agent and toolchain workbench for understanding, working with, and verifying software.

## What CORE does

- inspect repositories and understand their structure
- find relevant code using repository search and definition lookup
- reason across Git, GitHub, and web context
- operate with bounded evidence and explicit verification
- make safe file edits behind approval and review the resulting diff

## Status

This repository is the working CORE source tree (current version in `pyproject.toml`; check it at runtime with `core --version`).

## Local development

```bash
.venv/bin/pip install -e .
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/ tests/
```

## Notes

- This repository has not yet been published to GitHub as the first public CORE release.
- The project is designed for local execution and inspection, with real verification before publication.
