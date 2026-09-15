# CORE

CORE is a terminal-native developer agent and toolchain workbench for understanding, working with, and verifying software.

## What CORE does

- inspect repositories and understand their structure
- find relevant code using repository search and definition lookup
- reason across Git, GitHub, and web context
- operate with bounded evidence and explicit verification
- support safe file edits behind approval and review the resulting diff

## Status

This repository is the working CORE source tree for the v0.4.0 milestone.

## Local development

```bash
.venv/bin/pip install -e .
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/ tests/
```

## Notes

- This repository is not yet published to GitHub as the first public CORE release.
- The project is designed for local execution and inspection, with real verification before publication.
