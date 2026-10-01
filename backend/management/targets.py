"""
Bracket target selection (owner resolution R9).

With several active targets the FULL quantity is bracketed on the one most likely to be reached;
partial exits and scaling are excluded from the MVP. "Likelihood" is `Target.confidence`
(0..1). Ties fall back to the target nearest entry (price must travel through nearer levels
first, so a nearer target is never less likely to be reached), then to the lowest `priority` number.
No active target -> None (stop-only bracket).
"""

from __future__ import annotations

from typing import Optional, Sequence

from backend.core.models.target import Target


def select_bracket_target(targets: Sequence[Target], entry_price: float) -> Optional[Target]:
    active = [t for t in targets if t.active]
    if not active:
        return None
    return min(active, key=lambda t: (-t.confidence, abs(t.price - entry_price), t.priority))
