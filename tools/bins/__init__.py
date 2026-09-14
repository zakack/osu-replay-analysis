"""Deterministic binning over extracted replay features.

Extraction and simulation happen in C#; this package is the classification layer that
turns per-object features into cells, compares those cells against a reference corpus,
and emits a findings table. Nothing here selects, ranks or narrates — that is the one
layer allowed to be fuzzy, and it reads this package's output rather than its inputs.
"""
from .schema import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION"]
