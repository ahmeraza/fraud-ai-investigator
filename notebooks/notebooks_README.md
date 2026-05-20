# Notebooks

Exploratory analysis and component validation notebooks for the Fraud AI Investigator project.

These notebooks are **read-only documentation** — they do not run in production.
All production logic lives in `app/` as importable Python modules.

---

## Contents

| Notebook | Phase | Purpose |
|---|---|---|
| `phase1_data_exploration.ipynb` | Phase 1 | Synthetic dataset validation, distribution analysis, baseline rule performance |
| `phase2_alert_engine.ipynb` | Phase 2 | Alert engine rule validation, API integration testing, precision/recall benchmarking |

## When to use notebooks vs `.py` files

| Task | Use |
|---|---|
| Exploring data distributions | Notebook |
| Validating a new rule or model interactively | Notebook |
| Writing reusable business logic | `.py` module in `app/` |
| Writing tests | `.py` file in `tests/` |
| Anything run by the API or CI pipeline | `.py` file |

## Running notebooks

```bash
# From project root
source .venv/bin/activate
uv run jupyter notebook notebooks/
```

The API must be running before executing Phase 2+ notebooks:

```bash
uv run uvicorn app.main:app --reload
```

## Conventions

- Each notebook has a clear **Objective**, **Outcome**, and **Limitations** section at the top
- Every section explains the *why*, not just the *what*
- A completion checklist cell at the end validates all expected outputs were produced
- No notebook imports from another notebook — shared logic is extracted to `app/` modules
