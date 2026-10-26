"""Refresh catalog/ec2.json from the public instances.vantage.sh dataset (built from the AWS pricing API).

    python scripts/refresh_catalog.py [path/to/instances.json]

Keeps general/compute/memory families commonly used for EKS nodes, sizes large..4xlarge, Linux on-demand
prices for a few regions, and the EKS max-pods value (VPC CNI without prefix delegation).
"""
import datetime as dt
import json
import pathlib
import sys
import urllib.request

SOURCE = "https://instances.vantage.sh/instances.json"
FAMILIES = ["m6i", "c6i", "r6i", "m7i", "c7i", "r7i", "m7g", "c7g", "r7g", "t3"]
SIZES = ["medium", "large", "xlarge", "2xlarge", "4xlarge"]
REGIONS = ["us-east-1", "ap-south-1", "eu-west-1"]
OUT = pathlib.Path(__file__).resolve().parents[1] / "catalog" / "ec2.json"


def load(path: str | None) -> list[dict]:
    if path:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    with urllib.request.urlopen(SOURCE, timeout=60) as r:  # noqa: S310 (fixed https URL)
        return json.loads(r.read())


def main() -> None:
    data = load(sys.argv[1] if len(sys.argv) > 1 else None)
    types = []
    for x in data:
        name = x["instance_type"]
        fam, _, size = name.partition(".")
        if fam not in FAMILIES or size not in SIZES or not x.get("max_pods"):
            continue
        prices = {}
        for region in REGIONS:
            try:
                prices[region] = float(x["pricing"][region]["linux"]["ondemand"])
            except (KeyError, TypeError, ValueError):
                pass
        if not prices:
            continue
        types.append({
            "name": name,
            "vcpu": int(x["vCPU"]),
            "memory_gib": float(x["memory"]),
            "max_pods": int(x["max_pods"]),
            "arch": "arm64" if fam.endswith("g") else "amd64",
            "price_hour": prices,
        })
    types.sort(key=lambda t: (t["name"].split(".")[0], t["vcpu"]))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "source": SOURCE,
        "fetched": dt.date.today().isoformat(),
        "note": "Linux on-demand USD/hour. max_pods = EKS VPC CNI default (no prefix delegation).",
        "types": types,
    }, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(types)} instance types to {OUT}")


if __name__ == "__main__":
    main()
