"""Exact minimum number of nodes by branch and bound (for small instances, to measure heuristic quality).

Depth-first search over pods sorted by size. Each pod goes into an existing node with room or into one new
node. Pruning:
  * bound: stop a branch as soon as it uses as many nodes as the best solution found so far
  * symmetry: nodes with identical remaining capacity (and the same anti-affinity keys) are interchangeable, so
    try only one of them, and a pod opens at most one new node (all empty nodes are identical)
  * early exit: when the best solution equals the lower bound it is optimal
Worst case is exponential; a node budget keeps it bounded (returns the best found and optimal=False).
"""
from __future__ import annotations

from dataclasses import dataclass

from capplan.model import NodeType, Pod, Resources
from capplan.packing import _sorted_decreasing, best_fit_decreasing, check_fits, lower_bound


@dataclass
class ExactResult:
    nodes: int
    optimal: bool
    explored: int


def exact_min_nodes(pods: list[Pod], nt: NodeType, overhead: Resources = Resources(), overhead_pods: int = 0,
                    budget: int = 2_000_000) -> ExactResult:
    check_fits(pods, nt, overhead)
    room = nt.allocatable - overhead
    max_pods = nt.max_pods - overhead_pods
    items = [(p.req.cpu_m, p.req.mem_mi, p.spread) for p in _sorted_decreasing(pods, room)]
    lb = lower_bound(pods, nt, overhead, overhead_pods)
    best = len(best_fit_decreasing(pods, nt, overhead, overhead_pods))   # good initial upper bound
    if best == lb:
        return ExactResult(best, True, 0)

    free_cpu: list[int] = []
    free_mem: list[int] = []
    count: list[int] = []
    keys: list[frozenset[str]] = []
    explored = 0

    def dfs(i: int) -> None:
        nonlocal best, explored
        explored += 1
        if explored > budget or best == lb:
            return
        if len(free_cpu) >= best:
            return
        if i == len(items):
            best = len(free_cpu)
            return
        c, m, g = items[i]
        seen: set[tuple[int, int, int, frozenset[str]]] = set()
        for b in range(len(free_cpu)):
            key = (free_cpu[b], free_mem[b], count[b], keys[b])
            if key in seen or c > free_cpu[b] or m > free_mem[b] or count[b] >= max_pods or (g and g in keys[b]):
                continue
            seen.add(key)
            free_cpu[b] -= c
            free_mem[b] -= m
            count[b] += 1
            before = keys[b]
            if g:
                keys[b] = before | {g}
            dfs(i + 1)
            free_cpu[b] += c
            free_mem[b] += m
            count[b] -= 1
            keys[b] = before
        if len(free_cpu) + 1 < best:
            free_cpu.append(room.cpu_m - c)
            free_mem.append(room.mem_mi - m)
            count.append(1)
            keys.append(frozenset({g}) if g else frozenset())
            dfs(i + 1)
            free_cpu.pop()
            free_mem.pop()
            count.pop()
            keys.pop()

    dfs(0)
    return ExactResult(best, explored <= budget, explored)
