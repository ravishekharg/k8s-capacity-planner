"""Read workloads and nodes from Kubernetes (`kubectl get pods -A -o json` output or a live cluster).

Effective pod request follows the scheduler's rules:
  regular containers are summed; init containers run one at a time, so only the largest counts;
  native sidecars (init containers with restartPolicy: Always) keep running, so they add to both;
  pod overhead (RuntimeClass) is added on top.
    request = max(sum(app) + sum(sidecars), max_i(init_i + sidecars started before i)) + overhead
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from capplan.model import Pod, Resources

_MEM = {"": 1, "k": 1000, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}


def parse_cpu(q: str | int | float | None) -> int:
    """'250m' -> 250, '0.5' -> 500, 2 -> 2000 (millicores, rounded up)."""
    if q in (None, ""):
        return 0
    s = str(q)
    if s.endswith("m"):
        return int(float(s[:-1]) + 0.999999)
    return int(float(s) * 1000 + 0.999999)


def parse_mem(q: str | int | float | None) -> int:
    """'512Mi' -> 512, '1Gi' -> 1024, '1G' -> 954, '1e9' -> 954 (MiB, rounded up)."""
    if q in (None, ""):
        return 0
    m = re.fullmatch(r"([0-9.eE+-]+)([KMGT]i?|k)?", str(q))
    if not m:
        raise ValueError(f"bad memory quantity {q!r}")
    num, unit = m.groups()
    b = float(num) * _MEM[unit or ""]
    return int(-(-b // 1024**2))


def _req(container: dict) -> Resources:
    r = (container.get("resources") or {}).get("requests") or {}
    return Resources(parse_cpu(r.get("cpu")), parse_mem(r.get("memory")))


def effective_request(spec: dict) -> Resources:
    app = sum((_req(c) for c in spec.get("containers", [])), Resources())
    sidecars = Resources()
    init_peak = Resources()
    for c in spec.get("initContainers", []) or []:
        if c.get("restartPolicy") == "Always":          # native sidecar
            sidecars = sidecars + _req(c)
            continue
        r = _req(c) + sidecars
        init_peak = Resources(max(init_peak.cpu_m, r.cpu_m), max(init_peak.mem_mi, r.mem_mi))
    steady = app + sidecars
    total = Resources(max(steady.cpu_m, init_peak.cpu_m), max(steady.mem_mi, init_peak.mem_mi))
    ov = spec.get("overhead") or {}
    return total + Resources(parse_cpu(ov.get("cpu")), parse_mem(ov.get("memory")))


@dataclass
class Workload:
    pods: list[Pod]                 # pods the planner must place
    daemon_overhead: Resources      # requests of one pod per DaemonSet (present on every node)
    daemonsets: int
    defaulted: int                  # pods without requests that got the default
    skipped: int                    # finished, static/mirror pods


def _owner_kind(meta: dict) -> str:
    refs = meta.get("ownerReferences") or []
    return refs[0]["kind"] if refs else ""


def workload_from_pod_list(doc: dict, default: Resources = Resources(100, 128)) -> Workload:
    pods, ds_req = [], {}
    defaulted = skipped = 0
    for item in doc.get("items", []):
        meta, spec = item["metadata"], item["spec"]
        phase = (item.get("status") or {}).get("phase")
        kind = _owner_kind(meta)
        if phase in ("Succeeded", "Failed") or kind == "Node":       # finished, or static (mirror) pods
            skipped += 1
            continue
        req = effective_request(spec)
        if kind == "DaemonSet":
            ds = (meta.get("ownerReferences") or [{}])[0].get("name", meta["name"])
            prev = ds_req.get(ds, Resources())
            ds_req[ds] = Resources(max(prev.cpu_m, req.cpu_m), max(prev.mem_mi, req.mem_mi))
            continue
        if req == Resources():
            req, defaulted = default, defaulted + 1
        pods.append(Pod(meta["name"], req, meta.get("namespace", "default")))
    return Workload(pods, sum(ds_req.values(), Resources()), len(ds_req), defaulted, skipped)


def kubectl_json(*args: str) -> dict:
    out = subprocess.run(["kubectl", *args, "-o", "json"], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def current_nodes(doc: dict) -> list[str]:
    """Instance types of the cluster's current worker nodes (label node.kubernetes.io/instance-type)."""
    out = []
    for n in doc.get("items", []):
        labels = n["metadata"].get("labels", {})
        if "node-role.kubernetes.io/control-plane" in labels:
            continue
        out.append(labels.get("node.kubernetes.io/instance-type", "unknown"))
    return out
