"""Round times the way the Digi team writes them: 15/30/45 mins, 1 hour, 1.5 hours, 2 hours, 3 hours..."""

import math

STEPS = [15, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360, 420, 480]  # minutes; above this, whole hours


TOLERANCE = 3  # minutes: a value this little over a step stays on it (31 min -> 30, not 45)


def round_up(minutes: float, tolerance: float = TOLERANCE) -> int:
    if minutes <= 0:
        return 0
    for s in STEPS:
        if minutes <= s + tolerance:
            return s
    return math.ceil(minutes / 60) * 60


def round_down(minutes: float) -> int:
    if minutes < STEPS[0]:
        return STEPS[0] if minutes > 0 else 0
    for s in reversed(STEPS):
        if minutes >= s:
            return s if s == STEPS[-1] or minutes < STEPS[-1] else math.floor(minutes / 60) * 60
    return STEPS[0]


def duration(minutes: int) -> str:
    """45 -> '45 mins', 60 -> '1 hour', 90 -> '1.5 hours'."""
    if minutes < 60:
        return f"{minutes} mins"
    hours = minutes / 60
    text = f"{hours:g}"
    return f"{text} hour" if hours == 1 else f"{text} hours"


def time_range(low_minutes: float, high_minutes: float) -> str:
    """33-49 -> '30–60 min'; 50-100 -> '0.75–2 hr'... minutes while it fits in an hour, else hours."""
    lo, hi = round_down(low_minutes), round_up(high_minutes)
    if lo == hi:
        return duration(hi)
    if hi <= 60:
        return f"{lo}–{hi} min"
    lo_hours = max(math.floor(low_minutes / 30) * 0.5, 0.5)  # half hours, at least 0.5
    return f"{lo_hours:g}–{hi / 60:g} hr"
