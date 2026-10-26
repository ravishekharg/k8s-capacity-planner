"""Markdown / JSON rendering of plans."""
from __future__ import annotations

from capplan.fleet import Plan
from capplan.kube import Workload
from capplan.model import Resources


def _gib(mi: int) -> str:
    return f"{mi / 1024:.1f} GiB"


def plan_dict(p: Plan) -> dict:
    cpu, mem = p.utilisation()
    return {"plan": p.label, "nodes": len(p.nodes), "types": p.type_counts(), "lower_bound": p.lower_bound,
            "cpu_util": round(cpu, 3), "mem_util": round(mem, 3), "cost_month_usd": round(p.cost_month, 2)}


def markdown(w: Workload, plans: list[Plan], mixed: Plan, region: str, strategy: str,
             current_cost: float | None, top: int = 8) -> str:
    total = sum((p.req for p in w.pods), Resources())
    best = plans[0]
    lines = [
        "# Capacity plan", "",
        f"Region **{region}**, heuristic **{strategy.upper()}**, on-demand prices.", "",
        "## Workload", "",
        f"- {len(w.pods)} pods to place, requesting **{total.cpu_m / 1000:.1f} vCPU** and **{_gib(total.mem_mi)}**",
        f"- {w.daemonsets} DaemonSets reserve {w.daemon_overhead.cpu_m}m CPU / "
        f"{w.daemon_overhead.mem_mi} MiB on every node",
        f"- {w.defaulted} pods had no requests (default applied), {w.skipped} finished/static pods skipped", "",
        "## Single-instance-type plans (cheapest first)", "",
        "| Instance type | Nodes | Lower bound | CPU used | Memory used | USD / month |",
        "|---|---|---|---|---|---|",
    ]
    for p in plans[:top]:
        cpu, mem = p.utilisation()
        lines.append(f"| {p.label} | {len(p.nodes)} | {p.lower_bound} | {cpu:.0%} | {mem:.0%} | {p.cost_month:,.0f} |")
    cpu, mem = mixed.utilisation()
    mix = ", ".join(f"{n} x {t}" for t, n in mixed.type_counts().items())
    lines += [
        "", "## Recommendation", "",
        f"- **Simplest:** {len(best.nodes)} x {best.label} = **${best.cost_month:,.0f}/month**",
        f"- **Cheapest (right-sized mixed fleet):** {mix} = **${mixed.cost_month:,.0f}/month** "
        f"(CPU {cpu:.0%}, memory {mem:.0%} of allocatable)",
    ]
    if current_cost:
        saving = current_cost - mixed.cost_month
        lines.append(f"- Current nodes cost **${current_cost:,.0f}/month**: potential saving **${saving:,.0f}/month** "
                     f"({saving / current_cost:.0%})")
    lines += ["", "Allocatable capacity uses the EKS AMI reservation formulas (kube-reserved + eviction threshold). "
              "Keep headroom for rollouts (maxSurge) and failure of one node (N+1) with `--headroom`."]
    return "\n".join(lines) + "\n"
