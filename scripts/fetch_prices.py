"""Pull Bedrock on-demand prices for ap-southeast-2 from the AWS Price List
API and write them into evals/reports/prices.json.

The cost model is the part a CFO will press hardest on, so the numbers come
from AWS's own published price list with the publication date attached, not
from memory or a blog post.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "reports" / "prices.json"

WANTED = {
    "Nova Micro": "amazon.nova-micro",
    "Nova Lite": "amazon.nova-lite",
    "Nova Pro": "amazon.nova-pro",
    "Titan Text Embeddings V2": "amazon.titan-embed-text-v2",
    "Claude Haiku 4.5": "anthropic.claude-haiku-4-5",
    "Claude Sonnet 4.5": "anthropic.claude-sonnet-4-5",
}


def main() -> None:
    pricing = boto3.client("pricing", region_name="us-east-1")
    found: dict[str, dict] = {}
    pub = None

    paginator = pricing.get_paginator("get_products")
    for page in paginator.paginate(
            ServiceCode="AmazonBedrock",
            Filters=[{"Type": "TERM_MATCH", "Field": "regionCode",
                      "Value": "ap-southeast-2"}]):
        for blob in page["PriceList"]:
            item = json.loads(blob)
            attrs = item["product"]["attributes"]
            model = attrs.get("model", "")
            itype = attrs.get("inferenceType", "")
            if model not in WANTED:
                continue
            # Standard on-demand only: skip "priority", "flex", "batch",
            # "cache" variants, which are different products.
            if itype not in ("Input tokens", "Output tokens"):
                continue
            for term in item["terms"]["OnDemand"].values():
                for dim in term["priceDimensions"].values():
                    price = float(dim["pricePerUnit"]["USD"])
                    key = "in" if itype == "Input tokens" else "out"
                    found.setdefault(model, {"model_id_prefix": WANTED[model]})
                    found[model][key] = price
                    found[model]["unit"] = dim["unit"]
            pub = item.get("publicationDate", pub)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"region": "ap-southeast-2", "published": pub, "prices": found},
        indent=2, sort_keys=True))

    print(f"AWS published price list, ap-southeast-2, published {pub}\n")
    print(f"{'model':30} {'USD / 1K in':>12} {'USD / 1K out':>13}")
    for model, p in sorted(found.items()):
        print(f"{model:30} {p.get('in', 0):>12.6f} {p.get('out', 0):>13.6f}")
    print(f"\nwritten -> {OUT}")


if __name__ == "__main__":
    main()
