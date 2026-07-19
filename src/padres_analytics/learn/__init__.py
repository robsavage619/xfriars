"""Self-learning priors — turn editorial decisions into ranking evidence.

Every verdict the system already produces (Board queued/dismissed, Studio
rejections, referee blocks, prediction grades, engagement) was being collected
and never read back. This package closes that loop.

Priors bias *ranking only*. They never touch facts, never alter a claim, and
never relax a statistical gate.
"""

from __future__ import annotations
