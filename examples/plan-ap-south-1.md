# Capacity plan

Region **ap-south-1**, heuristic **BFD**, on-demand prices.

## Workload

- 164 pods to place, requesting **59.6 vCPU** and **102.5 GiB**
- 0 DaemonSets reserve 0m CPU / 0 MiB on every node
- 0 pods had no requests (default applied), 0 finished/static pods skipped

## Single-instance-type plans (cheapest first)

| Instance type | Nodes | Lower bound | CPU used | Memory used | USD / month |
|---|---|---|---|---|---|
| c6i.4xlarge | 4 | 4 | 94% | 88% | 1,986 |
| c7i.4xlarge | 4 | 4 | 94% | 88% | 2,085 |
| c6i.xlarge | 17 | 16 | 90% | 86% | 2,110 |
| c7i.xlarge | 17 | 16 | 90% | 86% | 2,215 |
| c6i.2xlarge | 9 | 8 | 84% | 76% | 2,234 |
| c7i.2xlarge | 9 | 8 | 84% | 76% | 2,345 |
| m6i.4xlarge | 4 | 4 | 94% | 42% | 2,359 |
| m6i.2xlarge | 8 | 8 | 94% | 41% | 2,359 |

## Recommendation

- **Simplest:** 4 x c6i.4xlarge = **$1,986/month**
- **Cheapest (right-sized mixed fleet):** 4 x c6i.4xlarge = **$1,986/month** (CPU 94%, memory 88% of allocatable)

Allocatable capacity uses the EKS AMI reservation formulas (kube-reserved + eviction threshold). Keep headroom for rollouts (maxSurge) and failure of one node (N+1) with `--headroom`.
