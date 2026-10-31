# CANDIDATE-BUILT STAND-IN — held-out perturbation fixtures

The rubric says the organizer *may* supply held-out perturbations of the same input schema.
None are published in this draft, so the fixtures here are the candidate's own stand-ins. They
are **not** organizer material, carry no provenance, and exist only so the test suite can show
the pipeline is a general procedure over the schema rather than arithmetic tied to the eleven
demo rows. Replace or delete them when the organizer's perturbations exist.

Each directory holds a complete input pack (`case.json`, `quotes.csv`, `performance.csv`) and an
`expected.json` with the node statuses (and, where meaningful, the decision and exclusions) the
pipeline must reproduce. `tests/test_pipeline.py::test_standin_perturbation_fixtures_run` runs
every directory.

| Fixture | Perturbation | What it exercises |
| --- | --- | --- |
| `no-forwards` | all firm forwards removed | current-month anchor only; implied/modeled/forecast labels; default κ; wider bands |
| `extra-rows-and-currency` | extra quote rows (a Nov bid, an EUR ask, an unknown column), optional fields blanked | schema tolerance; explicit currency exclusion; two-sided Nov node |
| `shifted-tenors` | as-of 2026-09-01, tenors [2, 4, 6] | tenor arithmetic from a different month start; interpolation; strip handling |

All numbers are fictional, derived from the organizer's synthetic demo pack.
