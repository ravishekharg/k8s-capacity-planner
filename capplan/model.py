"""Core types. Resources are integers (CPU in millicores, memory in MiB) so arithmetic is exact."""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from capplan.allocatable import allocatable_cpu_m, allocatable_mem_mi

DEFAULT_CATALOG = pathlib.Path(__file__).resolve().parents[1] / "catalog" / "ec2.json"
HOURS_PER_MONTH = 730


@dataclass(frozen=True, order=True)
class Resources:
    cpu_m: int = 0
    mem_mi: int = 0

    def __add__(self, o: Resources) -> Resources:
        return Resources(self.cpu_m + o.cpu_m, self.mem_mi + o.mem_mi)

    def __sub__(self, o: Resources) -> Resources:
        return Resources(self.cpu_m - o.cpu_m, self.mem_mi - o.mem_mi)

    def fits_in(self, cap: Resources) -> bool:
        return self.cpu_m <= cap.cpu_m and self.mem_mi <= cap.mem_mi


@dataclass(frozen=True)
class Pod:
    name: str
    req: Resources
    namespace: str = "default"


@dataclass(frozen=True)
class NodeType:
    name: str
    vcpu: int
    memory_gib: float
    max_pods: int
    price_hour: float
    arch: str = "amd64"

    @property
    def allocatable(self) -> Resources:
        """What pods can actually request on an EKS node (capacity minus kube/system reserved and eviction)."""
        return Resources(allocatable_cpu_m(self.vcpu), allocatable_mem_mi(int(self.memory_gib * 1024), self.max_pods))

    @property
    def price_month(self) -> float:
        return self.price_hour * HOURS_PER_MONTH


@dataclass
class Node:
    """A bin: one node of a given type with the pods placed on it."""
    type: NodeType
    overhead: Resources = field(default_factory=Resources)      # DaemonSet requests present on every node
    pods: list[Pod] = field(default_factory=list)
    used: Resources = field(default_factory=Resources)

    def __post_init__(self) -> None:
        self.used = self.used + self.overhead

    @property
    def free(self) -> Resources:
        return self.type.allocatable - self.used

    def can_fit(self, pod: Pod, overhead_pods: int = 0) -> bool:
        return pod.req.fits_in(self.free) and len(self.pods) + overhead_pods < self.type.max_pods

    def add(self, pod: Pod) -> None:
        self.pods.append(pod)
        self.used = self.used + pod.req


def load_catalog(region: str, path: pathlib.Path = DEFAULT_CATALOG, families: list[str] | None = None,
                 allow_arm: bool = False) -> list[NodeType]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for t in data["types"]:
        fam = t["name"].split(".")[0]
        if region not in t["price_hour"] or (families and fam not in families):
            continue
        if t["arch"] == "arm64" and not allow_arm:
            continue
        out.append(NodeType(t["name"], t["vcpu"], t["memory_gib"], t["max_pods"], t["price_hour"][region], t["arch"]))
    if not out:
        raise ValueError(f"no instance types for region={region} families={families}")
    return sorted(out, key=lambda n: n.price_hour)
