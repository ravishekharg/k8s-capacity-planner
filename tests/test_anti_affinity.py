"""Required pod anti-affinity (one replica per node): packing, bounds, the exact solver and parsing."""
import itertools
import random

from hypothesis import given, settings
from hypothesis import strategies as st

from capplan.exact import exact_min_nodes
from capplan.kube import spread_key, workload_from_pod_list
from capplan.model import NodeType, Pod, Resources
from capplan.packing import (
    best_fit_decreasing,
    first_fit_decreasing,
    first_fit_decreasing_tree,
    lower_bound,
    validate,
)
from capplan.workloads import microservices, pod_list_json

M6I_LARGE = NodeType("m6i.large", 2, 8, 29, 0.096)
SMALL = NodeType("tiny", 2, 4, 10, 0.05)

# (cpu, memory, group): group 0 means no anti-affinity
pod_spec = st.tuples(st.integers(1, 1200), st.integers(1, 5000), st.integers(0, 4))


def pods_from(specs):
    return [Pod(f"p{i}", Resources(c, m), spread=f"g{g}" if g else "") for i, (c, m, g) in enumerate(specs)]


def test_replicas_that_must_be_apart_need_one_node_each():
    pods = [Pod(f"web-{i}", Resources(10, 10), spread="shop:app=web") for i in range(4)]
    assert lower_bound(pods, M6I_LARGE) == 4                     # they'd all fit on one node otherwise
    for fn in (first_fit_decreasing, first_fit_decreasing_tree, best_fit_decreasing):
        nodes = fn(pods, M6I_LARGE)
        validate(nodes, pods)
        assert len(nodes) == 4
    assert exact_min_nodes(pods, M6I_LARGE).nodes == 4


def test_groups_share_nodes_with_each_other_and_with_free_pods():
    pods = ([Pod(f"a{i}", Resources(10, 10), spread="a") for i in range(3)]
            + [Pod(f"b{i}", Resources(10, 10), spread="b") for i in range(3)]
            + [Pod(f"x{i}", Resources(10, 10)) for i in range(6)])
    nodes = first_fit_decreasing_tree(pods, M6I_LARGE)
    validate(nodes, pods)
    assert len(nodes) == 3                                       # a_i + b_i + free pods together


@settings(max_examples=150, deadline=None)
@given(st.lists(pod_spec, min_size=1, max_size=40), st.integers(0, 3))
def test_packings_never_put_two_of_a_group_together(specs, overhead_pods):
    pods = pods_from(specs)
    lb = lower_bound(pods, M6I_LARGE, overhead_pods=overhead_pods)
    for fn in (first_fit_decreasing, first_fit_decreasing_tree, best_fit_decreasing):
        nodes = fn(pods, M6I_LARGE, overhead_pods=overhead_pods)
        validate(nodes, pods, overhead_pods)                     # includes the anti-affinity check
        assert len(nodes) >= lb


@settings(max_examples=200, deadline=None)
@given(st.lists(pod_spec, min_size=1, max_size=120), st.integers(0, 3))
def test_segment_tree_still_matches_linear_first_fit_exactly(specs, overhead_pods):
    pods = pods_from(specs)
    scan = first_fit_decreasing(pods, M6I_LARGE, overhead_pods=overhead_pods)
    tree = first_fit_decreasing_tree(pods, M6I_LARGE, overhead_pods=overhead_pods)
    assert [[p.name for p in n.pods] for n in scan] == [[p.name for p in n.pods] for n in tree]


def brute_force_min_nodes(pods, nt):
    """Try every assignment of pods to k nodes, smallest k first (tiny instances only)."""
    room = nt.allocatable
    for k in range(1, len(pods) + 1):
        for assign in itertools.product(range(k), repeat=len(pods)):
            ok = True
            for b in range(k):
                on = [p for p, a in zip(pods, assign, strict=True) if a == b]
                keys = [p.spread for p in on if p.spread]
                if (sum(p.req.cpu_m for p in on) > room.cpu_m or sum(p.req.mem_mi for p in on) > room.mem_mi
                        or len(on) > nt.max_pods or len(keys) != len(set(keys))):
                    ok = False
                    break
            if ok:
                return k
    raise AssertionError("unreachable")


@settings(max_examples=60, deadline=None)
@given(st.integers(0, 10_000))
def test_exact_solver_is_optimal_with_anti_affinity(seed):
    rnd = random.Random(seed)
    cap = SMALL.allocatable
    pods = [Pod(f"p{i}", Resources(rnd.randint(50, cap.cpu_m * 6 // 10), rnd.randint(64, cap.mem_mi * 6 // 10)),
                spread=rnd.choice(["", "", "g1", "g2"])) for i in range(6)]
    ex = exact_min_nodes(pods, SMALL)
    assert ex.optimal
    assert ex.nodes == brute_force_min_nodes(pods, SMALL)
    assert lower_bound(pods, SMALL) <= ex.nodes <= len(best_fit_decreasing(pods, SMALL))


def _term(match=None, exprs=None, key="kubernetes.io/hostname"):
    sel = {}
    if match:
        sel["matchLabels"] = match
    if exprs:
        sel["matchExpressions"] = exprs
    return {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"labelSelector": sel, "topologyKey": key}]}}


def test_spread_key_from_pod_specs():
    meta = {"namespace": "shop", "labels": {"app": "web", "tier": "front"}}
    assert spread_key(meta, {"affinity": _term({"app": "web"})}) == "shop:app=web"
    assert spread_key(meta, {"affinity": _term(exprs=[{"key": "app", "operator": "In", "values": ["web"]}])}) \
        == "shop:app=web"
    assert spread_key(meta, {"affinity": _term({"app": "web"}, key="topology.kubernetes.io/zone")}) == ""   # zones
    assert spread_key(meta, {"affinity": _term({"app": "db"})}) == ""            # keeps away from others only
    assert spread_key(meta, {"affinity": _term(exprs=[{"key": "app", "operator": "NotIn", "values": ["x"]}])}) == ""
    preferred = {"podAntiAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": [
        {"weight": 100, "podAffinityTerm": {"labelSelector": {"matchLabels": {"app": "web"}},
                                            "topologyKey": "kubernetes.io/hostname"}}]}}
    assert spread_key(meta, {"affinity": preferred}) == ""                      # soft: the scheduler may ignore it
    assert spread_key(meta, {}) == ""


def test_ha_sample_round_trips_through_pod_json():
    pods = microservices(40, 7, ha=True)
    back = workload_from_pod_list(pod_list_json(pods)).pods
    assert [p.spread for p in back] == [p.spread for p in pods]
    groups = {p.spread for p in pods if p.spread}
    assert groups and all(sum(p.spread == g for p in pods) >= 3 for g in groups)
