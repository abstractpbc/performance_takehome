"""Candidate-owned unattended optimization harness for the Abstract take-home (Task 2).

Everything in this package is candidate-built. It drives a propose -> build ->
measure -> keep/revert loop against an inference engine container (`llmd` on
the real target, or the CPU `standin` engine used for development evidence).
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
