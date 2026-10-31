# Compute Quant — synthetic submission guide

This is a self-contained implementation of the supplied synthetic assignment.
It needs Python 3.11+ and the standard library, with no GPU, account, network request,
paid dataset or additional organizer material. Every quote and economic assumption is
fictional. Engineering completion does not establish real market fair value or an
official grading result.

Read [REPORT.md](REPORT.md) for the decision memo, product definition, complete
normalization/exclusion trail, market/cost reconciliation, cash flows and sensitivities.
The base case does not support signing at the offered rate, while the scarcity case
changes that conclusion. The interval spans both gains and losses; it is a judgmental
scenario range, not a calibrated confidence interval.

| Artifact | Purpose |
| --- | --- |
| [curve.csv](curve.csv) | Required 1/3/6/12-month delivery nodes, units, evidence status, ranges and scenarios |
| [valuation.json](valuation.json) | Full assumptions, normalization, curve, reservation valuation and stresses |
| [normalization.csv](normalization.csv) | Uncapped row-by-row quote treatment |
| [run-manifest.json](run-manifest.json) | Exact input/source hashes, generation environment and parameters |
| [submission.json](submission.json) | Reproduction entry point, source identity and artifact paths |
| [audit/independent_cashflows.py](audit/independent_cashflows.py) | Separate calendar/cash-flow/cost/scenario and byte-provenance reconciliation; imports none of the model |

Important distinctions when reading the results:

- An observed one-sided ask is evidence for one edge of a range. Its displayed midpoint
  is estimated; the six- and twelve-month levels are modeled/forecast, not quoted forwards.
- Reserved capacity is billed for all calendar hours, regardless of expected use.
- Supply cost is a soft modeling anchor, not a guaranteed market-price floor or cap.
- The current-month flexibility comparison requires matched delivery/service/payment
  terms. Today's flexible price is not a bound on future guaranteed capacity.
- Counterparty default can make an above-market reservation less negative for the buyer
  under the declared release-of-both-sides assumption. This is not a universal credit rule;
  consequential disruption costs are outside the supplied case.

## Reproduce

Use a canonical LF checkout (`git -c core.autocrlf=false clone ...`) so actual file bytes
match the recorded input/source hashes. Run from the repository root:

```sh
python -B tasks/compute-quant/research/run.py \
  --case tasks/compute-quant/data/demo-case.json \
  --out /tmp/abstract-quant-reproduction \
  --report /tmp/abstract-quant-reproduction/REPORT.md
python -B -m unittest discover -s tasks/compute-quant/tests -t tasks/compute-quant -v
python -B tasks/compute-quant/audit/independent_cashflows.py
python -B -m tools.check_integrity --base 81aead87d78ed468c4c374fb42921471b44c3f80
```

Choose a fresh output directory for each rerun. `curve.csv`, `valuation.json`,
`normalization.csv` and `REPORT.md` reproduce byte-for-byte. Run-manifest environment,
command/output path and generation time describe that new run; set `SOURCE_DATE_EPOCH`
to pin its timestamp. The research tree/hash is compared separately from the artifact
commit that records the generated outputs.

The independent checker intentionally reconstructs the supplied demo's zero-recovery
cash flows and scenario formulas. It verifies arithmetic and recorded bytes, not the
economic truth of the scenario assumptions, real tradability, forecast accuracy or
every possible extension of the input schema. The broader test suite exercises input
perturbations, malformed data, identification and recovery cases.

The documented candidate-defined schemas, digest convention and optional input
extensions are usable as supplied. The absent organizer tag, official receipt and
held-out grading fixtures are recorded limitations; no substitute organizer approval,
market evidence or official score is asserted.

## Final review fixes — 28 October 2026

The final review corrected omitted supply-anchor defaults, report rendering without the optional cheap-compute scenario, and complete exclusion reasons in the uncapped normalization CSV. The numerical curve, valuation and report remain byte-identical to the prior delivery.

A genuine clean Linux Git checkout of source `12833e8982c1f08bb8748c2d674e381339404154` passed all 173 candidate tests, root tests, protected-file integrity and the independent cash-flow/source-byte audit. Two fresh runs reproduced the curve, valuation, normalization CSV and report byte-for-byte. The run manifest and submission record that committed research tree and its actual LF source bytes.
