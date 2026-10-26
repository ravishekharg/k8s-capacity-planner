"""EKS-optimized AMI reservation formulas (kubelet kube-reserved + eviction threshold).

CPU reserved:  6% of the 1st core, 1% of the 2nd, 0.5% of the 3rd and 4th, 0.25% of every core above 4.
Memory reserved: 255 MiB + 11 MiB per max pod, plus the 100 MiB hard eviction threshold.
Example (m5/m6i.large: 2 vCPU, 8 GiB, 29 pods): 1930m CPU and 7518 MiB allocatable.
"""


def reserved_cpu_m(vcpu: int) -> int:
    m = 0.0
    for core in range(1, vcpu + 1):
        m += 1000 * (0.06 if core == 1 else 0.01 if core == 2 else 0.005 if core <= 4 else 0.0025)
    return round(m)


def allocatable_cpu_m(vcpu: int) -> int:
    return vcpu * 1000 - reserved_cpu_m(vcpu)


def allocatable_mem_mi(memory_mi: int, max_pods: int, eviction_mi: int = 100) -> int:
    return memory_mi - (255 + 11 * max_pods) - eviction_mi
