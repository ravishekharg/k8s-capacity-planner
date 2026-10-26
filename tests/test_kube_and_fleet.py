import json

import pytest

from capplan.cli import main
from capplan.fleet import recommend, right_size, single_type_plans
from capplan.kube import effective_request, parse_cpu, parse_mem, workload_from_pod_list
from capplan.model import Resources, load_catalog
from capplan.workloads import microservices, pod_list_json


@pytest.mark.parametrize("q,want", [("250m", 250), ("0.5", 500), ("2", 2000), (1, 1000), ("1.5m", 2), (None, 0)])
def test_parse_cpu(q, want):
    assert parse_cpu(q) == want


@pytest.mark.parametrize("q,want", [("512Mi", 512), ("1Gi", 1024), ("1G", 954), ("1e9", 954), ("128974848", 123),
                                    ("64Ki", 1), (None, 0)])
def test_parse_mem(q, want):
    assert parse_mem(q) == want


def c(cpu, mem, **kw):
    return {"name": "x", "resources": {"requests": {"cpu": cpu, "memory": mem}}, **kw}


def test_effective_request_uses_max_of_init_and_sum_of_apps():
    spec = {"containers": [c("200m", "256Mi"), c("100m", "128Mi")],
            "initContainers": [c("1", "64Mi"), c("50m", "1Gi")]}
    assert effective_request(spec) == Resources(1000, 1024)


def test_native_sidecars_add_to_steady_state_and_later_inits():
    spec = {"containers": [c("200m", "256Mi")],
            "initContainers": [c("100m", "100Mi", restartPolicy="Always"), c("500m", "200Mi")],
            "overhead": {"cpu": "10m", "memory": "10Mi"}}
    # steady = 200+100 = 300m, init peak = 500+100 = 600m; memory: steady 356, init 300 -> 356
    assert effective_request(spec) == Resources(610, 366)


def test_workload_separates_daemonsets_static_and_finished_pods():
    doc = {"items": [
        {"metadata": {"name": "web-1", "namespace": "a", "ownerReferences": [{"kind": "ReplicaSet", "name": "web"}]},
         "spec": {"containers": [c("250m", "512Mi")]}, "status": {"phase": "Running"}},
        {"metadata": {"name": "noreq", "namespace": "a"}, "spec": {"containers": [{"name": "x"}]},
         "status": {"phase": "Running"}},
        {"metadata": {"name": "node-exp-1", "namespace": "mon",
                      "ownerReferences": [{"kind": "DaemonSet", "name": "node-exporter"}]},
         "spec": {"containers": [c("50m", "64Mi")]}, "status": {"phase": "Running"}},
        {"metadata": {"name": "node-exp-2", "namespace": "mon",
                      "ownerReferences": [{"kind": "DaemonSet", "name": "node-exporter"}]},
         "spec": {"containers": [c("50m", "64Mi")]}, "status": {"phase": "Running"}},
        {"metadata": {"name": "etcd-cp", "namespace": "kube-system",
                      "ownerReferences": [{"kind": "Node", "name": "cp"}]},
         "spec": {"containers": [c("100m", "100Mi")]}, "status": {"phase": "Running"}},
        {"metadata": {"name": "job-1", "namespace": "a"}, "spec": {"containers": [c("1", "1Gi")]},
         "status": {"phase": "Succeeded"}},
    ]}
    w = workload_from_pod_list(doc)
    assert [p.name for p in w.pods] == ["web-1", "noreq"]
    assert w.daemonsets == 1 and w.daemon_overhead == Resources(50, 64)
    assert w.defaulted == 1 and w.skipped == 2


def test_right_sizing_never_costs_more():
    pods = microservices(30, seed=3)
    types = load_catalog("us-east-1", families=["m6i", "c6i", "r6i"])
    for plan in single_type_plans(pods, types)[:5]:
        assert right_size(plan, types).cost_month <= plan.cost_month + 1e-9


def test_recommend_and_cli_end_to_end(tmp_path):
    pods_file = tmp_path / "pods.json"
    pods_file.write_text(json.dumps(pod_list_json(microservices(40, seed=7))))
    out = tmp_path / "plan.json"
    assert main(["plan", "--pods", str(pods_file), "--region", "ap-south-1", "--families", "m6i,c6i,r6i",
                 "--format", "json", "--out", str(out)]) == 0
    result = json.loads(out.read_text())
    best = result["plans"][0]
    assert best["nodes"] >= best["lower_bound"] and result["mixed"]["cost_month_usd"] <= best["cost_month_usd"]
    plans, mixed = recommend(microservices(40, seed=7), load_catalog("ap-south-1", families=["m6i", "c6i", "r6i"]))
    assert plans == sorted(plans, key=lambda p: (p.cost_month, len(p.nodes)))
