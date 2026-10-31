"""Compute Quant research pipeline (candidate-owned, stdlib only).

Offline, deterministic pipeline that normalizes dated GPU-capacity quotes, builds a
labeled forward curve, derives a transparent supply-cost view, and values the supplied
capacity reservation. Entry point: ``run.py`` / :mod:`research.cli`.
"""

__version__ = "1.0.0"
