"""Demo search_catalog handler for configurable replay."""

from __future__ import annotations


def run(arguments: dict) -> dict:
    query = arguments.get("query", "")
    return {
        "query": query,
        "items": [
            {"sku": "lamp-01", "name": "Desk Lamp", "price": 39.0},
            {"sku": "lamp-02", "name": "Task Light", "price": 52.0},
        ],
    }
