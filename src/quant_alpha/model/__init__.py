"""Model subpackage.

Keep package init lightweight to avoid importing optional heavy deps (e.g. lightgbm)
when callers only need governance/drift utilities.
"""

__all__ = []
