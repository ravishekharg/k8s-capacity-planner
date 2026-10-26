"""Measure heuristic quality against the exact optimum, and heuristic speed at cluster scale."""
from __future__ import annotations

import random
import time

from capplan.exact import exact_min_nodes
from capplan.model import NodeType
from capplan.packing import best_fit_decreasing, first_fit_decreasing, first_fit_decreasing_tree, lower_bound
from capplan.workloads import random_pods


def quality(nt: NodeType, trials: int = 300, pods: int = 14, seed: int = 1) -> dict:
    rnd = random.Random(seed)
    stats = {"trials": trials, "pods_per_trial": pods, "node_type": nt.name, "exact_proven": 0,
             "ffd": {"optimal": 0, "extra_nodes": 0, "worst_extra": 0},
             "bfd": {"optimal": 0, "extra_nodes": 0, "worst_extra": 0},
             "lb_equals_opt": 0, "explored_max": 0}
    for _ in range(trials):
        ps = random_pods(pods, nt.allocatable, rnd)
        ex = exact_min_nodes(ps, nt)
        stats["exact_proven"] += ex.optimal
        stats["explored_max"] = max(stats["explored_max"], ex.explored)
        stats["lb_equals_opt"] += lower_bound(ps, nt) == ex.nodes
        for name, fn in (("ffd", first_fit_decreasing), ("bfd", best_fit_decreasing)):
            extra = len(fn(ps, nt)) - ex.nodes
            s = stats[name]
            s["optimal"] += extra == 0
            s["extra_nodes"] += extra
            s["worst_extra"] = max(s["worst_extra"], extra)
    return stats


def speed(nt: NodeType, sizes: tuple[int, ...] = (100, 1000, 5000, 20000), seed: int = 2) -> list[dict]:
    rnd = random.Random(seed)
    out = []
    for n in sizes:
        ps = random_pods(n, nt.allocatable, rnd, max_frac=0.25)
        row = {"pods": n, "lower_bound": lower_bound(ps, nt)}
        for name, fn in (("ffd_scan", first_fit_decreasing), ("ffd_tree", first_fit_decreasing_tree),
                         ("bfd", best_fit_decreasing)):
            if n > 5000 and name != "ffd_tree":      # the O(n*b) variants take minutes here
                continue
            t = time.perf_counter()
            row[name + "_nodes"] = len(fn(ps, nt))
            row[name + "_ms"] = round((time.perf_counter() - t) * 1000, 1)
        out.append(row)
    return out
