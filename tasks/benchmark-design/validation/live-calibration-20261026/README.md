# Real MI355X baseline calibration

The pinned LLMD/Qwen3.6-27B baseline completed 60 measured requests, eight warmups and
one valid startup generation. The evaluator and independent timing audit both passed.
This is calibration evidence; the full suite, official quick mode, real A/A verdict,
comparison power and the required full-suite runtime remain unproved.

Measured source: `0174ca423cc583f4b8356284a79fdf4cbfb19a24`.
Actual Task 1 image: `sha256:4afe375c77e07d20d83c381db0b1441b7929ca51c0a97dadb5db56477253b023`.
This differs from Task 2's separately measured baseline image.

| Observation | Result |
| --- | --- |
| Original calibration-mode start | 2026-10-26 08:13:49.335036 UTC |
| Capture wrapper start | 08:30:39.661961 UTC |
| Startup to completed valid generation | 24.313114 seconds |
| Short calibration TTFT median | 0.0459 seconds |
| Long calibration TTFT median | 0.3129 seconds |
| Fit samples / distinct approximate lengths | 60 / 26 |
| Observed length domain / scoring domain | 44–4117 / 44–4081 |
| Nonnegative OLS intercept / slope | 0.05917245663 s / 0.000254172645 s per approximate prompt token |
| Largest positive fitted residual | 0.02686809471 seconds |

The fit is an observed envelope, not a calibrated confidence or prediction interval.
Prompt counts remain a regex covariate without the engine chat template. Output counts
use pinned Tokenizers 0.22.2 re-encoding and delta-level timing, not generated token IDs
or individual token arrival timestamps. `engine.kind=unknown` in the original recorder
is retained; the pinned image, model, AMD device and HTTP evidence establish the actual
deployment separately. No field was relabelled to make an evaluator pass.

`calibration.tar.gz` preserves the original raw requests, run record, plan, report,
independent audit, logs, capture receipt, ownership record and export manifest. Its
SHA-256 is `d36c4906ea0c6eda48ce1ebbbea68481e88251f72e62a51c2288aa46a5bbf2b5`
(121,885 bytes). `evidence.json` pins the other exact source bytes. The frozen plan is
`2301607e75b78ae0bdb800cd6e55e0ac9c09b0517e7df71971dcde4f2ad2c589`;
the fit receipt is `d402d86de4942cf2d25fce7f9e719bcad35553f30d948d01a09a09e18de9b88d`.

The first isolated offhost receiver exposed a missing explicit `importlib.util`
import in private controller tooling. The complete unchanged archive was independently
verified and promoted with that stdlib import supplied; the original failure/partial
remain retained. No calibration was recaptured. A fresh CPU replay then passed all four
CLI paths—archive verification, evaluation, independent timing and fitting—and reproduced
both the frozen plan and fit receipt byte for byte (`fresh-replay-receipt.json`).

To replay with the dependencies and pinned target tokenizer snapshot described in
`../../EXECUTION.md`, run from `tasks/benchmark-design`. Set `TOKENIZER` to that
verified target snapshot directory. The original capture and successful replays used
**CPython 3.14.4, Unicode database 16.0.0 and Tokenizers 0.22.2**. The Unicode version
is part of the recorded tokenizer identity; another version correctly refuses these
unchanged raw events. Use that interpreter/runtime and check it before replay:

```sh
TOKENIZER='/path/to/verified/target-tokenizer-snapshot'
python -c 'import sys, unicodedata, tokenizers; assert sys.version_info[:3] == (3, 14, 4); assert unicodedata.unidata_version == "16.0.0"; assert tokenizers.__version__ == "0.22.2"'
ARCHIVE='validation/live-calibration-20261026/calibration.tar.gz'
python suite/capture.py verify-export "$ARCHIVE" 2000000
RAW="$(mktemp -d)"
NEW="$(mktemp -d)"
tar -xzf "$ARCHIVE" -C "$RAW"
python suite/evaluate.py --events "$RAW/events" --workload "$RAW/workload.json" \
  --tokenizer-snapshot "$TOKENIZER" --out "$NEW/report.json"
python suite/audit_timing.py --events "$RAW/events" --out "$NEW/audit.json"
PYTHONPATH=suite python -m bench.calibration --plan "$RAW/workload.json" --events "$RAW/events" \
  --tokenizer-snapshot "$TOKENIZER" --out-plan "$NEW/frozen-plan.json" --out-receipt "$NEW/fit.json"
```

The two temporary directories retain the extracted evidence and new outputs for
inspection; do not reuse existing output files. No GPU or model weights are needed
for this replay. The original scored plan remains frozen. A later
AA-only diagnostic failed its own deadline after partial requests; it is neither a
complete quick run nor evidence of a clean/noisy A/A decision. The original full suite
was not admitted within the remaining work window. Shared preparation, export,
cleanup and offhost verification cannot be omitted from a future three-hour claim.

The failed diagnostic retained exactly one startup probe and two unscored chat
warmups, with no measured requests. Both chat outputs consumed the full 256-token
cap and ended mid-sentence. Their prompt and request-body hashes match the pinned
source and profile, and their text/usage/protocol request checks pass under the
declared gates. Those gates allow `finish_reason=length` and check the nonce plus
minimum text length; they do not establish semantic prose completeness. Treat
these rows as bounded decoding, without an A/A, stability or power verdict. A
future concise-answer task or prose-completion gate would change the workload and
require a new plan identity, fresh calibration and matched comparisons; it cannot
be applied retroactively to these frozen events.
