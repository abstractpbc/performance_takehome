# Experimental batch 1 immutable delivery

The canonical-loop batch 1 image is additionally published as:

`ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:93f4b72539b0e9cec3a21ed9c2c0c1ec74e876791318456bebf36182a52ce2c9`

Publication ran at exact head `41456da6d144abea96aada865703286a02b935f8` in [workflow41051877610](https://github.com/sharonbasovich/performance_takehome/actions/runs/41051877610). The [publication job](https://github.com/sharonbasovich/performance_takehome/actions/runs/41051877610/job/124049683001), [anonymous complete-graph job](https://github.com/sharonbasovich/performance_takehome/actions/runs/41051877610/job/124049797139), and [actual anonymous Docker pull/inspect/CPU-help job](https://github.com/sharonbasovich/performance_takehome/actions/runs/41051877610/job/124050991123) all passed. The temporary owned help container was confirmed absent.

The anonymous checker streamed and SHA-verified all 25 complete OCI objects: manifest, config and23 inherited layers, totaling5,923,167,708 bytes. Docker's actual config ID is `sha256:baeb12d7ad78daa8ff91486696ae94056542576afbceff24bfcc75db7857a0e0`. Retained primary receipts, all progress/journal/log bytes in three small original proof ZIPs, exact workflow/job links and independently checked hashes are [in publication/](publication/VERIFIED.json). [The independent review](publication/INDEPENDENT-REVIEW.json) is copied byte for byte, SHA `bd310ed01ceac84df6fd51862fd197e262516f28f936dbd92f92abd232cbae16`.

The complete 5.9 GB bodies were streamed and hashed on the pinned hosted runner; this local review checks retained proof bytes and does not claim a second local rehash of every layer. Publication and CPU help establish delivery, not an official performance grade. This experimental reference does not replace the recommended batch 4 image or turn its controller-selected configuration into a Pi choice. The source generator's later code-only follow-up did not republish or rerun these functional jobs; this proof remains bound to the original 41456da execution.
