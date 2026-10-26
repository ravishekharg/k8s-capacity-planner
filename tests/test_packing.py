import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from capplan.allocatable import allocatable_cpu_m, allocatable_mem_mi
from capplan.exact import exact_min_nodes
from capplan.model import NodeType, Pod, Resources
from capplan.packing import (
    best_fit_decreasing,
    first_fit_decreasing,
    first_fit_decreasing_tree,
    lower_bound,
    validate,
)
from capplan.workloads import random_pods

M6I_LARGE = NodeType("m6i.large", 2, 8, 29, 0.096)
SMALL = NodeType("tiny", 2, 4, 10, 0.05)


def test_eks_allocatable_matches_aws_docs():
    # AWS documents 1930m CPU and 574Mi kube-reserved memory for a 2 vCPU / 29-pod node
    assert allocatable_cpu_m(2) == 1930
    assert allocatable_mem_mi(8192, 29) == 8192 - 574 - 100
    assert allocatable_cpu_m(16) == 16000 - 110


def test_lower_bound_takes_the_tightest_dimension():
    cap = M6I_LARGE.allocatable
    cpu_heavy = [Pod(f"c{i}", Resources(cap.cpu_m // 2, 100)) for i in range(6)]       # 3 nodes by CPU
    mem_heavy = [Pod(f"m{i}", Resources(10, cap.mem_mi // 4)) for i in range(9)]       # 3 nodes by memory
    many_tiny = [Pod(f"t{i}", Resources(1, 1)) for i in range(60)]                     # 3 nodes by pod count
    assert lower_bound(cpu_heavy, M6I_LARGE) == 3
    assert lower_bound(mem_heavy, M6I_LARGE) == 3
    assert lower_bound(many_tiny, M6I_LARGE) == 3


def test_pod_too_big_for_node_type_is_rejected():
    with pytest.raises(ValueError):
        first_fit_decreasing([Pod("huge", Resources(4000, 100))], M6I_LARGE)


def test_daemonset_overhead_reduces_room():
    cap = M6I_LARGE.allocatable
    pods = [Pod(f"p{i}", Resources(cap.cpu_m // 2, 100)) for i in range(2)]
    assert len(first_fit_decreasing(pods, M6I_LARGE)) == 1
    assert len(first_fit_decreasing(pods, M6I_LARGE, overhead=Resources(100, 100))) == 2


def test_max_pods_limit_is_respected():
    pods = [Pod(f"p{i}", Resources(1, 1)) for i in range(25)]
    nodes = best_fit_decreasing(pods, SMALL, overhead_pods=2)       # 8 app pods per node
    validate(nodes, pods, overhead_pods=2)
    assert len(nodes) == 4


def test_exact_beats_or_matches_heuristics_on_a_known_trap():
    # Classic case where first fit by size is not optimal: three pairs that only fit together crosswise
    cap = SMALL.allocatable
    a, b = cap.cpu_m * 6 // 10, cap.cpu_m * 4 // 10
    m1, m2 = cap.mem_mi * 4 // 10, cap.mem_mi * 6 // 10
    pods = [Pod(f"a{i}", Resources(a, m1)) for i in range(3)] + [Pod(f"b{i}", Resources(b, m2)) for i in range(3)]
    ex = exact_min_nodes(pods, SMALL)
    assert ex.optimal and ex.nodes == 3
    assert len(best_fit_decreasing(pods, SMALL)) >= ex.nodes


pod_req = st.tuples(st.integers(1, 1200), st.integers(1, 5000))


@settings(max_examples=150, deadline=None)
@given(st.lists(pod_req, min_size=1, max_size=40), st.integers(0, 3))
def test_heuristics_always_produce_valid_packings(reqs, overhead_pods):
    pods = [Pod(f"p{i}", Resources(c, m)) for i, (c, m) in enumerate(reqs)]
    lb = lower_bound(pods, M6I_LARGE, overhead_pods=overhead_pods)
    for fn in (first_fit_decreasing, best_fit_decreasing):
        nodes = fn(pods, M6I_LARGE, overhead_pods=overhead_pods)
        validate(nodes, pods, overhead_pods)
        assert len(nodes) >= lb


@settings(max_examples=60, deadline=None)
@given(st.integers(0, 10_000))
def test_exact_is_between_lower_bound_and_heuristics(seed):
    pods = random_pods(9, M6I_LARGE.allocatable, random.Random(seed))
    ex = exact_min_nodes(pods, M6I_LARGE)
    assert ex.optimal
    assert lower_bound(pods, M6I_LARGE) <= ex.nodes <= len(best_fit_decreasing(pods, M6I_LARGE))
    assert ex.nodes <= len(first_fit_decreasing(pods, M6I_LARGE))


@settings(max_examples=200, deadline=None)
@given(st.lists(pod_req, min_size=1, max_size=120), st.integers(0, 3))
def test_segment_tree_first_fit_matches_linear_first_fit_exactly(reqs, overhead_pods):
    pods = [Pod(f"p{i}", Resources(c, m)) for i, (c, m) in enumerate(reqs)]
    scan = first_fit_decreasing(pods, M6I_LARGE, overhead_pods=overhead_pods)
    tree = first_fit_decreasing_tree(pods, M6I_LARGE, overhead_pods=overhead_pods)
    assert [[p.name for p in n.pods] for n in scan] == [[p.name for p in n.pods] for n in tree]
