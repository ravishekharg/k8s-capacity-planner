"""Two-dimensional (CPU, memory) bin packing with a per-node pod limit.

Placing pods onto the fewest nodes is vector bin packing, which is NP-hard, so production schedulers and
autoscalers use greedy heuristics. Implemented here:

- lower_bound            any packing needs at least this many nodes (per-dimension volume bound)
- first_fit_decreasing   sort pods by dominant share, put each into the first node it fits
- best_fit_decreasing    ... into the node it fits most tightly (least normalised slack left)
- first_fit_decreasing_tree   first fit decreasing accelerated with a segment tree (identical result)

Complexity: sorting O(n log n) + placement O(n * b) for n pods and b nodes with a linear scan, about
O(n log b) with the segment tree. The exact solver in exact.py measures how far these heuristics are from optimal.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from capplan.model import Node, NodeType, Pod, Resources


def dominant_share(pod: Pod, cap: Resources) -> float:
    """Largest fraction of a node this pod uses in any dimension (the DRF 'dominant resource')."""
    return max(pod.req.cpu_m / cap.cpu_m, pod.req.mem_mi / cap.mem_mi)


def check_fits(pods: Iterable[Pod], nt: NodeType, overhead: Resources = Resources()) -> None:
    room = nt.allocatable - overhead
    for p in pods:
        if not p.req.fits_in(room):
            raise ValueError(f"pod {p.namespace}/{p.name} ({p.req}) cannot fit on {nt.name} (room {room})")


def lower_bound(pods: list[Pod], nt: NodeType, overhead: Resources = Resources(), overhead_pods: int = 0) -> int:
    if not pods:
        return 0
    room = nt.allocatable - overhead
    total = sum((p.req for p in pods), Resources())
    return max(
        math.ceil(total.cpu_m / room.cpu_m),
        math.ceil(total.mem_mi / room.mem_mi),
        math.ceil(len(pods) / (nt.max_pods - overhead_pods)),
    )


def _sorted_decreasing(pods: list[Pod], cap: Resources) -> list[Pod]:
    # stable tie-break on name keeps results deterministic
    return sorted(pods, key=lambda p: (-dominant_share(p, cap), p.namespace, p.name))


def _pack(pods: list[Pod], nt: NodeType, choose: Callable[[list[Node], Pod], Node | None],
          overhead: Resources, overhead_pods: int) -> list[Node]:
    check_fits(pods, nt, overhead)
    nodes: list[Node] = []
    for pod in _sorted_decreasing(pods, nt.allocatable - overhead):
        node = choose(nodes, pod)
        if node is None:
            node = Node(nt, overhead)
            nodes.append(node)
        node.add(pod)
    return nodes


def first_fit_decreasing(pods: list[Pod], nt: NodeType, overhead: Resources = Resources(),
                         overhead_pods: int = 0) -> list[Node]:
    def first(nodes: list[Node], pod: Pod) -> Node | None:
        return next((n for n in nodes if n.can_fit(pod, overhead_pods)), None)
    return _pack(pods, nt, first, overhead, overhead_pods)


def best_fit_decreasing(pods: list[Pod], nt: NodeType, overhead: Resources = Resources(),
                        overhead_pods: int = 0) -> list[Node]:
    cap = nt.allocatable

    def slack_after(node: Node, pod: Pod) -> float:
        left = node.free - pod.req
        return left.cpu_m / cap.cpu_m + left.mem_mi / cap.mem_mi

    def best(nodes: list[Node], pod: Pod) -> Node | None:
        fitting = [n for n in nodes if n.can_fit(pod, overhead_pods)]
        return min(fitting, key=lambda n: slack_after(n, pod)) if fitting else None
    return _pack(pods, nt, best, overhead, overhead_pods)


class _FreeTree:
    """Segment tree over node slots 0..size-1 holding, per subtree, the max free CPU, max free memory and max
    free pod slots. Unopened slots hold an empty node's room, so the leftmost slot that fits is exactly the node
    first fit would choose (an existing node if one fits, otherwise the next new one).

    query: descend left first and skip subtrees whose maxima cannot hold the pod. The three maxima may come from
    different nodes, so a subtree can pass the check and still fail, which forces a backtrack: worst case O(b),
    typically O(log b). update: O(log b).
    """

    def __init__(self, size: int, cpu: int, mem: int, slots: int) -> None:
        self.n = 1
        while self.n < max(size, 1):
            self.n *= 2
        self.c = [cpu] * (2 * self.n)
        self.m = [mem] * (2 * self.n)
        self.s = [slots] * (2 * self.n)

    def first_fit(self, cpu: int, mem: int) -> int:
        def go(i: int) -> int:
            if self.c[i] < cpu or self.m[i] < mem or self.s[i] < 1:
                return -1
            if i >= self.n:
                return i - self.n
            left = go(2 * i)
            return left if left >= 0 else go(2 * i + 1)
        return go(1)

    def set(self, slot: int, cpu: int, mem: int, slots: int) -> None:
        i = slot + self.n
        self.c[i], self.m[i], self.s[i] = cpu, mem, slots
        i //= 2
        while i:
            a, b = 2 * i, 2 * i + 1
            self.c[i] = max(self.c[a], self.c[b])
            self.m[i] = max(self.m[a], self.m[b])
            self.s[i] = max(self.s[a], self.s[b])
            i //= 2


def first_fit_decreasing_tree(pods: list[Pod], nt: NodeType, overhead: Resources = Resources(),
                              overhead_pods: int = 0) -> list[Node]:
    """Same result as first_fit_decreasing, found in about O(n log n) instead of O(n * b)."""
    check_fits(pods, nt, overhead)
    room = nt.allocatable - overhead
    slots = nt.max_pods - overhead_pods
    tree = _FreeTree(len(pods), room.cpu_m, room.mem_mi, slots)
    nodes: list[Node] = []
    for pod in _sorted_decreasing(pods, room):
        i = tree.first_fit(pod.req.cpu_m, pod.req.mem_mi)
        if i == len(nodes):
            nodes.append(Node(nt, overhead))
        node = nodes[i]
        node.add(pod)
        tree.set(i, node.free.cpu_m, node.free.mem_mi, slots - len(node.pods))
    return nodes


STRATEGIES = {"ffd": first_fit_decreasing_tree, "ffd-scan": first_fit_decreasing, "bfd": best_fit_decreasing}


def validate(nodes: list[Node], pods: list[Pod], overhead_pods: int = 0) -> None:
    """Every pod placed exactly once and no node over capacity or over its pod limit."""
    placed = [p for n in nodes for p in n.pods]
    if sorted(map(id, placed)) != sorted(map(id, pods)):
        raise AssertionError("pods missing or placed twice")
    for n in nodes:
        if not n.used.fits_in(n.type.allocatable):
            raise AssertionError(f"node over capacity: {n.used} > {n.type.allocatable}")
        if len(n.pods) + overhead_pods > n.type.max_pods:
            raise AssertionError("node over its max-pods limit")
