"""Reproducible micro-benchmarks for athena.

Each module in this package exports a single ``run() -> dict``
function. The runner (``scripts/bench/runner.py``) discovers them
automatically; no central registry to maintain.

Benchmarks must:

- Run deterministically (or report distributions, not point values).
- Not require external network / providers — fixture-driven only.
- Return ``{"name": ..., "metric": ..., "unit": ..., "value": ...}``
  shape so the runner can compare across runs.

Add a new bench by dropping a module here with a ``run()``.
"""
