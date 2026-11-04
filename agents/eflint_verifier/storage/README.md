# eflint_verifier storage

Private storage of the `eflint_verifier` agent. Each verification is recorded under
`runs/<run_id>/`:

- `request.json` — the job request submitted to the external eFLINT model-checking service
  (after PNML normalisation and default-analysis generation);
- `report.json` — the interpreted outcome (verdict, per-property results, witnesses, renames) or
  the service error.

Override the location with `EFLINT_VERIFIER_RUNS_DIR`.
