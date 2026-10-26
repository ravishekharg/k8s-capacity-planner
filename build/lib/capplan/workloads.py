"""Synthetic but realistic microservice workloads (for demos, tests and benchmarks)."""
from __future__ import annotations

import random

from capplan.model import Pod, Resources

# (profile, cpu millicores, memory MiB, weight)
PROFILES = [
    ("web-small", 100, 128, 30),
    ("api", 250, 512, 30),
    ("api-large", 500, 1024, 15),
    ("jvm-service", 1000, 2048, 12),
    ("cache", 500, 4096, 5),
    ("worker", 2000, 1024, 5),
    ("search", 1500, 6144, 3),
]


def microservices(services: int = 40, seed: int = 7) -> list[Pod]:
    """`services` deployments, each 2-6 replicas, with a profile picked by weight."""
    rnd = random.Random(seed)
    names, weights = [p[0] for p in PROFILES], [p[3] for p in PROFILES]
    prof = {p[0]: p for p in PROFILES}
    pods = []
    for s in range(services):
        kind = rnd.choices(names, weights)[0]
        _, cpu, mem, _ = prof[kind]
        for r in range(rnd.randint(2, 6)):
            pods.append(Pod(f"svc{s:02d}-{kind}-{r}", Resources(cpu, mem), namespace=f"team{s % 6}"))
    return pods


def random_pods(n: int, cap: Resources, rnd: random.Random, max_frac: float = 0.6) -> list[Pod]:
    """Uniformly random requests up to max_frac of a node (a hard benchmark distribution)."""
    return [Pod(f"p{i}", Resources(rnd.randint(50, int(cap.cpu_m * max_frac)),
                                   rnd.randint(64, int(cap.mem_mi * max_frac)))) for i in range(n)]


def pod_list_json(pods: list[Pod]) -> dict:
    """Render pods as a minimal `kubectl get pods -A -o json` document."""
    return {"items": [{
        "metadata": {"name": p.name, "namespace": p.namespace,
                     "ownerReferences": [{"kind": "ReplicaSet", "name": p.name.rsplit("-", 1)[0]}]},
        "spec": {"containers": [{"name": "app", "resources": {"requests": {
            "cpu": f"{p.req.cpu_m}m", "memory": f"{p.req.mem_mi}Mi"}}}]},
        "status": {"phase": "Running"},
    } for p in pods]}
