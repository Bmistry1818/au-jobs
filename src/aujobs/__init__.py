"""AI exposure of the Australian job market.

A three-stage pipeline:

1. :mod:`aujobs.extract` — download and parse Jobs and Skills Australia workbooks.
2. :mod:`aujobs.score`   — score each occupation 0-10 on AI exposure via the Claude API.
3. :mod:`aujobs.build`   — merge stats and scores into the treemap's ``data.json``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
