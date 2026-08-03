"""Demo get_inventory handler for configurable replay."""

from __future__ import annotations


def run(arguments: dict) -> dict:
    return {"sku": arguments.get("sku"), "available": 12}
