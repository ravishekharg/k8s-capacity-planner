#!/usr/bin/env bash
# End to end on a real cluster (kind): deploy e2e/workload.yaml, label worker nodes with EC2 instance types,
# run `capplan plan --live`, and assert the workload was read exactly as the scheduler sees it.
set -euo pipefail
cd "$(dirname "$0")/.."

kubectl apply -f e2e/workload.yaml
kubectl -n shop rollout status deploy/api deploy/orders deploy/legacy --timeout=180s
kubectl -n shop rollout status ds/log-agent --timeout=180s
kubectl -n shop wait --for=condition=complete job/done --timeout=180s

# pretend the kind workers are EC2 nodes so the "current cost" path is exercised
for n in $(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o name); do
  kubectl label "$n" node.kubernetes.io/instance-type=m6i.xlarge --overwrite
done

capplan plan --live --region us-east-1 --families m6i,c6i,r6i --format json --out plan.json
capplan plan --live --region us-east-1 --families m6i,c6i,r6i --out plan.md
cat plan.md

python - <<'EOF'
import json
p = json.load(open("plan.json"))
# kind's own pods: coredns (2, ReplicaSet), local-path-provisioner (1); static control-plane pods are skipped
shop = 4 + 2 + 1
checks = {
    "all shop pods + kind system pods read": p["pods"] >= shop,
    "daemonsets detected (log-agent + kindnet + kube-proxy)": p["daemonsets"] >= 3,
    "pod without requests defaulted": p["defaulted"] >= 1,
    "current cost computed from instance-type labels": (p["current_cost_month_usd"] or 0) > 0,
    "best plan respects lower bound": p["plans"][0]["nodes"] >= p["plans"][0]["lower_bound"],
    "mixed fleet not more expensive": p["mixed"]["cost_month_usd"] <= p["plans"][0]["cost_month_usd"],
}
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
assert all(checks.values())
EOF

# effective request of the orders pod: max(sum(app)+sidecar, init+sidecars-before) = max(120m, 300m) = 300m CPU,
# memory max(144, 32) = 144Mi
python - <<'EOF'
import json, subprocess
from capplan.kube import effective_request
from capplan.model import Resources
doc = json.loads(subprocess.check_output(["kubectl", "-n", "shop", "get", "pods", "-l", "app=orders", "-o", "json"]))
got = effective_request(doc["items"][0]["spec"])
print("orders effective request:", got)
assert got == Resources(300, 144), got
print("PASS init container + native sidecar request semantics")
EOF
