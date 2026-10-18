# Repository instructions

- Read `README.md` and `docs/` before making architectural changes.
- Keep source access explicitly reviewed, bounded, attributable, and policy compliant.
- Never commit secrets or log credentials. Synthetic data must be labelled.
- Run the relevant tests and Ruff after changes; report actual results.
- Keep connectors isolated behind `app.connectors.base.Connector`.
