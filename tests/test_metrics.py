"""Metrics tests. Every expected number below was worked out by hand
and the working is written next to it, so a reader can check it."""

import pytest

from app import metrics
from app.qloo import Entity


def pick(entity_id, affinity=None, popularity=None, genres=(), other_tags=()):
    tags = [{"id": f"urn:tag:genre:test:{g}", "name": g, "type": "urn:tag:genre:test"} for g in genres]
    tags += [{"id": f"urn:tag:keyword:qloo:{k}", "name": k, "type": "urn:tag:keyword:qloo"} for k in other_tags]
    return Entity(entity_id=entity_id, name=entity_id, affinity=affinity,
                  popularity=popularity, tags=tags)


# ---- mean_affinity -------------------------------------------------

def test_mean_affinity_known_values():
    picks = [pick("a", affinity=0.9), pick("b", affinity=0.7), pick("c", affinity=0.5)]
    result = metrics.mean_affinity(picks)
    assert result["value"] == pytest.approx(0.7)  # (0.9 + 0.7 + 0.5) / 3 = 2.1 / 3
    assert (result["n"], result["of"]) == (3, 3)


def test_mean_affinity_skips_unscored_picks_instead_of_counting_them_as_zero():
    picks = [pick("a", affinity=0.8), pick("b", affinity=None), pick("c", affinity=0.6)]
    result = metrics.mean_affinity(picks)
    assert result["value"] == pytest.approx(0.7)  # (0.8 + 0.6) / 2, NOT / 3
    assert (result["n"], result["of"]) == (2, 3)


def test_mean_affinity_with_no_scores_is_none_not_zero():
    result = metrics.mean_affinity([pick("a"), pick("b")])
    assert result["value"] is None
    assert (result["n"], result["of"]) == (0, 2)


def test_mean_affinity_of_empty_list_is_none():
    assert metrics.mean_affinity([]) == {"value": None, "n": 0, "of": 0}


def test_affinity_of_zero_is_a_real_score_and_is_counted():
    picks = [pick("a", affinity=0.0), pick("b", affinity=1.0)]
    result = metrics.mean_affinity(picks)
    assert result["value"] == pytest.approx(0.5)
    assert result["n"] == 2


# ---- median_popularity ---------------------------------------------

def test_median_popularity_odd_count_is_the_middle_value():
    picks = [pick("a", popularity=0.99), pick("b", popularity=0.10), pick("c", popularity=0.95)]
    # sorted: 0.10, 0.95, 0.99 -> middle is 0.95
    assert metrics.median_popularity(picks)["value"] == pytest.approx(0.95)


def test_median_popularity_even_count_averages_the_middle_two():
    picks = [pick("a", popularity=0.2), pick("b", popularity=0.4), pick("c", popularity=0.6), pick("d", popularity=1.0)]
    # middle two are 0.4 and 0.6 -> 0.5
    assert metrics.median_popularity(picks)["value"] == pytest.approx(0.5)


def test_median_popularity_is_not_dragged_by_one_obscure_pick():
    picks = [pick("a", popularity=0.999), pick("b", popularity=0.998), pick("c", popularity=0.01)]
    # the mean would be 0.669; the median stays at 0.998
    assert metrics.median_popularity(picks)["value"] == pytest.approx(0.998)


def test_median_popularity_skips_missing_and_says_so():
    picks = [pick("a", popularity=0.9), pick("b"), pick("c", popularity=0.7)]
    result = metrics.median_popularity(picks)
    assert result["value"] == pytest.approx(0.8)  # (0.7 + 0.9) / 2
    assert (result["n"], result["of"]) == (2, 3)


def test_median_popularity_with_no_data_is_none():
    assert metrics.median_popularity([pick("a")])["value"] is None
    assert metrics.median_popularity([]) == {"value": None, "n": 0, "of": 0}


def test_median_popularity_does_not_depend_on_order():
    a, b, c = pick("a", popularity=0.3), pick("b", popularity=0.9), pick("c", popularity=0.5)
    assert metrics.median_popularity([a, b, c])["value"] == metrics.median_popularity([c, b, a])["value"] == 0.5


# ---- tag_variety ---------------------------------------------------

def test_tag_variety_identical_tags_is_zero():
    picks = [pick("a", genres=["x", "y"]), pick("b", genres=["x", "y"])]
    assert metrics.tag_variety(picks)["value"] == pytest.approx(0.0)


def test_tag_variety_nothing_shared_is_one():
    picks = [pick("a", genres=["x"]), pick("b", genres=["y"])]
    assert metrics.tag_variety(picks)["value"] == pytest.approx(1.0)


def test_tag_variety_two_picks_half_shared():
    # a = {x, y}, b = {y, z}. Shared = {y} = 1. All = {x, y, z} = 3.
    # Distance = 1 - 1/3 = 2/3.
    picks = [pick("a", genres=["x", "y"]), pick("b", genres=["y", "z"])]
    assert metrics.tag_variety(picks)["value"] == pytest.approx(2 / 3)


def test_tag_variety_three_picks_averages_all_pairs():
    # a = {x, y}, b = {y, z}, c = {x, y}
    # a-b: 1 - 1/3 = 2/3
    # a-c: 1 - 2/2 = 0
    # b-c: 1 - 1/3 = 2/3
    # mean = (2/3 + 0 + 2/3) / 3 = 4/9
    picks = [pick("a", genres=["x", "y"]), pick("b", genres=["y", "z"]), pick("c", genres=["x", "y"])]
    result = metrics.tag_variety(picks)
    assert result["value"] == pytest.approx(4 / 9)
    assert (result["n"], result["of"]) == (3, 3)


def test_tag_variety_leaves_out_untagged_picks_and_says_so():
    # The untagged pick must not be treated as "shares nothing with anyone".
    picks = [pick("a", genres=["x"]), pick("b", genres=["x"]), pick("c")]
    result = metrics.tag_variety(picks)
    assert result["value"] == pytest.approx(0.0)
    assert (result["n"], result["of"]) == (2, 3)


def test_tag_variety_needs_two_tagged_picks():
    result = metrics.tag_variety([pick("a", genres=["x"]), pick("b")])
    assert result["value"] is None
    assert (result["n"], result["of"]) == (1, 2)


def test_tag_variety_ignores_non_genre_tags():
    # Same genres, wildly different keyword tags: variety is still 0.
    picks = [pick("a", genres=["x"], other_tags=["k1", "k2", "k3"]), pick("b", genres=["x"], other_tags=["k9"])]
    assert metrics.tag_variety(picks)["value"] == pytest.approx(0.0)


def test_tag_variety_does_not_depend_on_order():
    a, b, c = pick("a", genres=["x", "y"]), pick("b", genres=["y", "z"]), pick("c", genres=["q"])
    assert metrics.tag_variety([a, b, c])["value"] == pytest.approx(metrics.tag_variety([c, a, b])["value"])


# ---- overlap -------------------------------------------------------

def test_overlap_counts_shared_entity_ids():
    left = [pick("a"), pick("b"), pick("c")]
    right = [pick("b"), pick("c"), pick("d"), pick("e")]
    result = metrics.overlap(left, right)
    assert result["count"] == 2
    assert result["ids"] == ["b", "c"]
    assert (result["of_a"], result["of_b"]) == (3, 4)


def test_overlap_with_an_empty_list_is_zero():
    assert metrics.overlap([pick("a")], [])["count"] == 0


# ---- summarize -----------------------------------------------------

def test_summarize_returns_the_three_per_list_metrics():
    picks = [pick("a", affinity=0.5, popularity=0.5, genres=["x"]),
             pick("b", affinity=0.7, popularity=0.9, genres=["y"])]
    summary = metrics.summarize(picks)
    assert set(summary) == {"affinity", "popularity", "variety"}
    assert summary["affinity"]["value"] == pytest.approx(0.6)
    assert summary["popularity"]["value"] == pytest.approx(0.7)
    assert summary["variety"]["value"] == pytest.approx(1.0)
