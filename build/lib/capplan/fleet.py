"""Choose node types: pack the workload onto each candidate type, then right-size each node.

1. single-type plans: for every instance type that can hold the largest pod, pack with the chosen heuristic
   and price it (nodes x hourly price). Sorting the candidates by cost is O(t log t).
2. right-sizing: after packing, each node's load is fixed, so swap it for the cheapest type that still holds
   that load and pod count. The catalog is sorted by price, so this is a linear scan per node; the result is
   a mixed fleet that is never more expensive than the single-type plan it started from.
"""
from __future__ import annotations

from dataclasses import dataclass

from capplan.model import Node, NodeType, Pod, Resources
from capplan.packing import STRATEGIES, lower_bound, validate


@dataclass
class Plan:
    label: str
    nodes: list[Node]
    lower_bound: int

    @property
    def cost_month(self) -> float:
        return sum(n.type.price_month for n in self.nodes)

    def utilisation(self) -> tuple[float, float]:
        cap = sum((n.type.allocatable for n in self.nodes), Resources())
        used = sum((n.used for n in self.nodes), Resources())
        return (used.cpu_m / cap.cpu_m if cap.cpu_m else 0.0, used.mem_mi / cap.mem_mi if cap.mem_mi else 0.0)

    def type_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.nodes:
            out[n.type.name] = out.get(n.type.name, 0) + 1
        return dict(sorted(out.items()))


def single_type_plans(pods: list[Pod], types: list[NodeType], strategy: str = "bfd",
                      overhead: Resources = Resources(), overhead_pods: int = 0) -> list[Plan]:
    pack = STRATEGIES[strategy]
    largest = max((p.req for p in pods), default=Resources(), key=lambda r: (r.cpu_m, r.mem_mi))
    plans = []
    for nt in types:
        room = nt.allocatable - overhead
        if room.cpu_m <= 0 or room.mem_mi <= 0 or nt.max_pods <= overhead_pods:
            continue
        if any(not p.req.fits_in(room) for p in pods) or not largest.fits_in(room):
            continue
        nodes = pack(pods, nt, overhead, overhead_pods)
        validate(nodes, pods, overhead_pods)
        plans.append(Plan(nt.name, nodes, lower_bound(pods, nt, overhead, overhead_pods)))
    return sorted(plans, key=lambda pl: (pl.cost_month, len(pl.nodes)))


def right_size(plan: Plan, types: list[NodeType], overhead: Resources = Resources(),
               overhead_pods: int = 0) -> Plan:
    by_price = sorted(types, key=lambda t: t.price_hour)
    resized = []
    for node in plan.nodes:
        load = node.used                      # includes the DaemonSet overhead
        count = len(node.pods) + overhead_pods
        new_type = next((t for t in by_price
                         if load.fits_in(t.allocatable) and count <= t.max_pods
                         and t.price_hour <= node.type.price_hour), node.type)
        n = Node(new_type, overhead)
        for p in node.pods:
            n.add(p)
        resized.append(n)
    return Plan(f"{plan.label} + right-sized", resized, plan.lower_bound)


def recommend(pods: list[Pod], types: list[NodeType], strategy: str = "bfd", overhead: Resources = Resources(),
              overhead_pods: int = 0) -> tuple[list[Plan], Plan]:
    plans = single_type_plans(pods, types, strategy, overhead, overhead_pods)
    if not plans:
        raise ValueError("no instance type can hold the largest pod; widen --families")
    mixed = [right_size(p, types, overhead, overhead_pods) for p in plans[:5]]
    best_mixed = min(mixed, key=lambda pl: pl.cost_month)
    return plans, best_mixed
