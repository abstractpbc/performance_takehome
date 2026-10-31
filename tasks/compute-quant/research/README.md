# Compute Quant research pipeline (candidate-owned)

Offline, deterministic, **stdlib-only** pipeline for Task 3. No network access, no third-party
packages, Python 3.11+.

```bash
# from the repository root
python tasks/compute-quant/research/run.py \
  --case tasks/compute-quant/data/demo-case.json \
  --out tasks/compute-quant/outputs/ \
  --report tasks/compute-quant/outputs/REPORT.md      # optional: render the report too
```

| Flag | Meaning |
| --- | --- |
| `--case PATH` | case JSON; CSVs are resolved relative to it |
| `--out DIR` | writes `curve.csv`, `valuation.json`, `run-manifest.json`, `normalization.csv` |
| `--config PATH` | model parameters (default `research/config/default.json`) |
| `--report PATH` | render `REPORT.md` from the same results (never hand-edited) |
| `--exclude-quote ID` | drop a quote before modelling (evidence-removal experiments; repeatable) |
| `--as-of ISO` | override the cutoff (experiments only; recorded in the outputs) |

Exit codes: `0` success (warnings may be printed); `2` malformed/unsupported input or
configuration (message names the row/field), unsupported Python (< 3.11), or any unexpected
internal failure (reported as a one-line message, never a traceback). No other codes are used.
Non-fatal findings (exclusions, crossed markets, wide spreads) are printed as `warning:` lines
on stderr and recorded in `valuation.json`.

Set `SOURCE_DATE_EPOCH` to pin the manifest timestamp; `curve.csv` and `valuation.json` are
byte-identical across reruns regardless.

## Modules

| Module | Role |
| --- | --- |
| `schema.py` | typed records and validation for input `schema_version` 1 |
| `loader.py` | case/CSV loading, SHA-256 per file, candidate-defined input-pack digest |
| `months.py` | UTC month arithmetic with actual calendar hours |
| `comparability.py` | as-of status, product tier, contract class |
| `normalize.py` | currency → unit → fees → payment timing → quantity ladder |
| `supply_cost.py` | annuity-based full-cost breakeven and sensitivity grid |
| `curve.py` | anchors, labels, mean reversion, bands, scenarios |
| `crosscheck.py` | H200 translation, commitments, spot, flexibility premium, history |
| `valuation.py` | reserved hours, cash flows, discounting, SLA, default, mark, K\*, decision |
| `pipeline.py` | orchestration and output rendering |
| `report.py` | `REPORT.md` renderer |
| `cli.py`, `run.py` | argument parsing and entry point |

## Input schema extensions (candidate-defined, all optional)

The organizer's `data/README.md` schema is accepted unchanged. These optional additions are
also understood; absent fields mean *unknown*, never zero:

- quote columns `fee_usd_per_gpu_hour` (recurring) and `one_time_fee_usd` (amortized over the
  stated delivery window × GPU count; a one-time fee without a window excludes the quote);
- case field `fx_rates_to_usd`: `{"EUR": 1.10}` USD per unit of currency. A non-USD quote
  without an entry is **excluded with an explicit reason**, never converted silently;
- unknown quote columns are tolerated and echoed per quote under `unknown_columns`.

Unsupported values (unit, kind, side, payment, schema_version) are errors, not guesses.

Comparable quotes must also meet the reference CPU, RAM and network requirements using
the supplied `quote_common_terms`. Missing or inadequate evidence excludes the quote;
CPU/RAM common terms are per GPU, while network bandwidth is the stated service rate.
The quantity factor supports only the exact alternative GPU count present in comparable
arrears on-demand calibration evidence. Mixed node sizes do not identify a pooled factor,
and an 8-GPU factor is never extrapolated to a 16-GPU quote or different hardware.

Hourly quantity calibration additionally requires explicit `cancellation=anytime` on both
asks. A label such as `current_on_demand` alone proves no cancellation right; unsupported
or non-cancellable quotes without guaranteed reserved service are `unclassified_rights`.
`node_month` requires exactly one aligned UTC billing month. Longer monthly-rent windows
and advance-paid multi-month strips are explicitly excluded until a monthly payment
schedule is provided; the model does not dilute one monthly bill over an entire strip or
compound all payments to the last date.

## Output schema (candidate-defined, `schema_version` 1)

`curve.csv` — one row per case tenor: `tenor_months, delivery_month, delivery_start,
delivery_end, hours, unit, status, usd_per_gpu_hour_mid, usd_per_gpu_hour_low,
usd_per_gpu_hour_high, bid, ask, uncertainty_type, evidence_ids, scenario_base,
scenario_cheap_compute, scenario_scarcity, months_beyond_last_anchor, notes`.
`status` ∈ {observed, implied, modeled, forecast, unidentified}.

`valuation.json` — the complete results mapping: inputs and hashes, every quote with its
normalization steps and reasons, exclusions, curve nodes/anchors/reversion fit/warnings,
supply-cost view and grid, cross-checks, reservation terms, valuation walk, sensitivities,
and the configuration used.

Unavailable usage illustrations are JSON `null`; a zero used fraction stays zero and
never reduces reserved fees. A valid zero-survival stress still has a computed mark,
while its non-unique fair fixed price is `null` and marked unidentified in the default
sensitivity. Memo claims and current flexible-price comparisons use the actual case
results, including evidence outside the capped quote-example table.

`run-manifest.json` — timestamp, code version and source digest, git commit (if available),
Python version, command line, input hashes, parameters.

## Model parameters (`config/default.json`)

| Parameter | Default | Meaning |
| --- | --- | --- |
| `one_sided_spread_fraction` | 0.15 | spread estimate for a one-sided tenor (max with the widest observed two-sided spread) |
| `fit_min_tenor_months` | 1 | observed nodes used to fit the reversion speed |
| `default_reversion_per_month` | 0.10 | κ when the shape cannot identify it |
| `max_reversion_per_month` | 2.0 | cap on the fitted κ |
| `reversion_band_per_month` | [0, 0.30] | κ range spanned by the band beyond the last anchor |
| `horizon_vol_per_sqrt_month` | 0.08 | random-walk band around the last anchor interval |
| `model_horizon_months_before_forecast` | 6 | months beyond the last anchor before a node is labelled forecast |
| `anchors` / `scenarios` | see file | long-run anchors (cost-based or evidence-based) and the observed side each scenario uses |
| `sensitivities` | see file | grids reported in `valuation.json` and the report |

Every one of these is an assumption and is listed in the report as such.


## Output size caps and cost envelope

`valuation.json` is bounded regardless of input size: the per-quote table is capped at
`output.max_quote_rows_in_valuation_json` rows (default 200; `quotes.count`/`quotes.truncated`
say what was cut), exclusions are summarised by reason, and every cross-check list carries a
`count` plus at most 10 example entries with summary statistics instead of raw price lists.
The full per-quote normalization table is always written as `normalization.csv` next to
`valuation.json` (one row per quote; grows linearly). Measured on the demo schema with
synthetic rows cloned from the demo quotes (CPython 3.11, one core, this repository's
`tests/test_review_findings.py::RoundThreeTests::test_full_pipeline_scale_envelope` asserts
the 20k point stays under 60 s / 1 GB / 2 MB):

| quote rows | wall time | peak RSS | valuation.json | normalization.csv |
| --- | --- | --- | --- | --- |
| 1,011 | 0.1 s | 32 MB | 0.43 MB | 0.24 MB |
| 5,011 | 0.4 s | 64 MB | 0.52 MB | 1.21 MB |
| 20,011 | 1.6 s | 183 MB | 0.86 MB | 4.85 MB |
| 50,011 | 4.2 s | 424 MB | 1.57 MB | 12.15 MB |

Size envelope: `valuation.json` grows only with the number of distinct exclusion reasons and
the fixed example caps (about 0.5 MB + ~20 bytes per quote for the by-reason map, 1.6 MB at
50k rows); `normalization.csv` is ~250 bytes per quote and is the only artifact that grows
linearly; peak RSS is ~8 KB per quote on top of a ~30 MB baseline.

Outputs are rendered in memory first and then written atomically (temporary file + rename),
so a failed run leaves no partial artifact.
