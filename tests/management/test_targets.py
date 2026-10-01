from backend.core.models.target import Target
from backend.management.targets import select_bracket_target


def T(price, conf=0.5, prio=1, active=True):
    return Target(type="t", price=price, source="t", priority=prio, confidence=conf, active=active)


def test_no_targets_or_only_inactive_ones_means_stop_only():
    assert select_bracket_target([], 100.0) is None
    assert select_bracket_target([T(110.0, active=False)], 100.0) is None


def test_highest_confidence_wins_even_if_farther_or_lower_priority():
    far_likely = T(130.0, conf=0.9, prio=3)
    assert select_bracket_target([T(110.0, conf=0.4, prio=1), far_likely, T(120.0, conf=0.6, prio=2)], 100.0) is far_likely


def test_confidence_ties_fall_back_to_the_nearest_target():
    near = T(105.0, prio=2)
    assert select_bracket_target([T(120.0, prio=1), near, T(110.0, prio=3)], 100.0) is near
    short_near = T(95.0)
    assert select_bracket_target([T(80.0), short_near], 100.0) is short_near  # works below entry too


def test_full_ties_fall_back_to_lowest_priority_number():
    first = T(105.0, prio=1)
    assert select_bracket_target([T(95.0, prio=2), first], 100.0) is first


def test_inactive_targets_are_ignored_even_when_most_confident():
    usable = T(110.0, conf=0.3)
    assert select_bracket_target([T(105.0, conf=0.99, active=False), usable], 100.0) is usable
