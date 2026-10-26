"""capplan: plan the cheapest node fleet for a Kubernetes workload.

  capplan plan --pods pods.json --region ap-south-1          # from `kubectl get pods -A -o json`
  capplan plan --live --region us-east-1 --families m6i,c6i,r6i
  capplan bench --trials 300                                 # heuristics vs exact optimum
  capplan sample --services 40 --out pods.json               # synthetic microservice workload
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from capplan import bench, kube, workloads
from capplan.fleet import recommend
from capplan.model import DEFAULT_CATALOG, Pod, Resources, load_catalog
from capplan.report import markdown, plan_dict


def _with_headroom(pods: list[Pod], h: float) -> list[Pod]:
    if h <= 0:
        return pods
    return [Pod(p.name, Resources(int(p.req.cpu_m * (1 + h) + 0.999), int(p.req.mem_mi * (1 + h) + 0.999)),
                p.namespace) for p in pods]


def cmd_plan(a: argparse.Namespace) -> int:
    doc = kube.kubectl_json("get", "pods", "-A") if a.live else json.loads(pathlib.Path(a.pods).read_text())
    w = kube.workload_from_pod_list(doc, Resources(a.default_cpu, a.default_mem))
    pods = _with_headroom(w.pods, a.headroom)
    families = a.families.split(",") if a.families else None
    types = load_catalog(a.region, pathlib.Path(a.catalog), families, a.allow_arm)
    overhead_pods = w.daemonsets
    plans, mixed = recommend(pods, types, a.strategy, w.daemon_overhead, overhead_pods)

    current_cost = None
    if a.live:
        prices = {t.name: t.price_month for t in load_catalog(a.region, pathlib.Path(a.catalog), None, True)}
        current = kube.current_nodes(kube.kubectl_json("get", "nodes"))
        if current and all(t in prices for t in current):
            current_cost = sum(prices[t] for t in current)

    if a.format == "json":
        out = json.dumps({"region": a.region, "strategy": a.strategy, "pods": len(pods),
                          "daemonsets": w.daemonsets, "defaulted": w.defaulted,
                          "plans": [plan_dict(p) for p in plans], "mixed": plan_dict(mixed),
                          "current_cost_month_usd": current_cost}, indent=2)
    else:
        out = markdown(w, plans, mixed, a.region, a.strategy, current_cost)
    if a.out:
        pathlib.Path(a.out).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
    return 0


def cmd_bench(a: argparse.Namespace) -> int:
    nt = next(t for t in load_catalog("us-east-1", pathlib.Path(a.catalog)) if t.name == a.node_type)
    result = {"quality": bench.quality(nt, a.trials, a.pods, a.seed), "speed": bench.speed(nt)}
    out = json.dumps(result, indent=2)
    if a.out:
        pathlib.Path(a.out).write_text(out, encoding="utf-8")
    print(out)
    return 0


def cmd_sample(a: argparse.Namespace) -> int:
    doc = workloads.pod_list_json(workloads.microservices(a.services, a.seed))
    pathlib.Path(a.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"wrote {len(doc['items'])} pods to {a.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="capplan", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="recommend node types and counts")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pods", help="kubectl get pods -A -o json output")
    src.add_argument("--live", action="store_true", help="read the current kubectl context")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--families", help="comma list, e.g. m6i,c6i,r6i (default: all)")
    p.add_argument("--allow-arm", action="store_true", help="include Graviton (arm64) types")
    p.add_argument("--strategy", choices=["bfd", "ffd", "ffd-scan"], default="bfd")
    p.add_argument("--headroom", type=float, default=0.0, help="inflate requests, e.g. 0.15 for 15%%")
    p.add_argument("--default-cpu", type=int, default=100, help="millicores for pods without requests")
    p.add_argument("--default-mem", type=int, default=128, help="MiB for pods without requests")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_plan)

    b = sub.add_parser("bench", help="heuristics vs exact optimum")
    b.add_argument("--node-type", default="m6i.large")
    b.add_argument("--trials", type=int, default=300)
    b.add_argument("--pods", type=int, default=14)
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--out")
    b.set_defaults(fn=cmd_bench)

    s = sub.add_parser("sample", help="write a synthetic microservice workload")
    s.add_argument("--services", type=int, default=40)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", default="pods.json")
    s.set_defaults(fn=cmd_sample)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
