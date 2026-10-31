This figure displays the two original descriptive 95% intervals from the separate fixed-order Task1 diagnostic on one AMD MI355X host: 30 healthy A, 30 delayed, then 30 healthy B. It converts recorded seconds to milliseconds without recomputing statistics. Each panel has its own x-axis scale.

The diagnostic is supplemental evidence. It does not establish measured power, an official regression grade, or revalidation of the primary three-hour study.

From the repository root, reproduce with Python and Matplotlib installed:

```console
PACKET=tasks/benchmark-design/validation/engine-fixed90-supplemental-20261030
python "$PACKET/figures/plot_result.py.txt" --archive "$PACKET/capture.tar.gz" --out /tmp/fixed90-figure
```

The script checks the exact original archive and summary hashes before rendering. `FIGURE-PROVENANCE.json` retains the full original values and source hashes. The PNG and SVG contain no credentials, private filesystem paths, or unrelated data.

`plot_result.py.txt` preserves the exact optional archived producer bytes. Python can execute it directly; it is not active benchmark code or a core dependency. The supplied PNG/SVG and original provenance are unchanged.
