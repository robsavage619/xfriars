"""LLM-driven hypothesis discovery.

The fixed detectors can only find trends someone wrote a detector for. This
package moves Claude to the *front* of discovery: it proposes ``HypothesisSpec``
blocks (a constrained :class:`~padres_analytics.detect.registry.MetricSpec`),
which ride the existing scanner's statistical lenses and gates. Claude never
computes a number — it only proposes *what to look at* and the deterministic
engine measures whether it is rare.

The safety boundary is :func:`validate`: every spec carries LLM-authored SQL
fragments (``filter_sql``, ``derived_expr``) and is treated as untrusted data —
column/table allowlisted and tokenized against an operator whitelist before it
is allowed near the database.
"""

from __future__ import annotations

from padres_analytics.detect.hypothesis.spec import HypothesisSpec
from padres_analytics.detect.hypothesis.validate import ValidationResult, validate

__all__ = ["HypothesisSpec", "ValidationResult", "validate"]
