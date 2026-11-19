"""Group repeated span failures into deterministic review clusters.

Each cluster uses a documented signature. The signature combines span kind,
the tool or span subject name, and the stable failure message text. Distinct
messages stay in separate clusters even when the tool name matches.
"""

from __future__ import annotations

from typing import Any

FAILURE_SIGNATURE_VERSION = 1
DEFAULT_SAMPLE_LIMIT = 5


def stable_failure_message(
    *,
    error: str | None,
    outcome: str | None,
    name: str,
) -> str:
    """Return the same failure text the error timeline uses for one span."""

    if error:
        return error
    if outcome == "failure":
        return f"{name} reported a failure outcome"
    return f"{name} ended with status error"


def failure_signature(kind: str, subject: str, message: str) -> str:
    """Return the deterministic signature for one failed span.

    The signature version stays in the key so future rules can cluster
    old evidence the same way after an upgrade.
    """

    normalized_kind = kind.strip()
    normalized_subject = subject.strip()
    normalized_message = message.strip()
    return (
        f"v{FAILURE_SIGNATURE_VERSION}|{normalized_kind}|"
        f"{normalized_subject}|{normalized_message}"
    )


def _occurrence_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        item.get("started_at") or "",
        item.get("run_id") or "",
        item.get("span_id") or "",
    )


def build_failure_clusters(
    occurrences: list[dict[str, Any]],
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Cluster span failures by signature and attach counts and samples."""

    safe_sample = max(1, min(sample_limit, 50))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in occurrences:
        message = item["message"]
        subject = item.get("tool_name") or item.get("name") or ""
        signature = failure_signature(item.get("kind") or "", subject, message)
        grouped.setdefault(signature, []).append({**item, "signature": signature})

    clusters: list[dict[str, Any]] = []
    for signature, members in grouped.items():
        ordered = sorted(members, key=_occurrence_sort_key)
        run_ids = sorted({member["run_id"] for member in ordered})
        representative = ordered[0]
        kind = representative.get("kind") or ""
        subject = representative.get("tool_name") or representative.get("name") or ""
        clusters.append(
            {
                "signature": signature,
                "signature_version": FAILURE_SIGNATURE_VERSION,
                "kind": kind,
                "subject": subject,
                "message": representative["message"],
                "occurrence_count": len(ordered),
                "run_count": len(run_ids),
                "representative": _cluster_member(representative),
                "occurrences": [
                    _cluster_member(member) for member in ordered[:safe_sample]
                ],
            }
        )

    clusters.sort(
        key=lambda cluster: (
            -cluster["occurrence_count"],
            cluster["signature"],
        )
    )
    return {
        "signature_version": FAILURE_SIGNATURE_VERSION,
        "cluster_count": len(clusters),
        "occurrence_count": len(occurrences),
        "sample_limit": safe_sample,
        "clusters": clusters,
    }


def _cluster_member(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": item["run_id"],
        "span_id": item["span_id"],
        "agent_name": item.get("agent_name") or "",
        "name": item.get("name") or "",
        "kind": item.get("kind") or "",
        "tool_name": item.get("tool_name") or "",
        "status": item.get("status") or "",
        "message": item["message"],
        "started_at": item.get("started_at") or "",
        "run_href": f"/runs/{item['run_id']}",
        "span_href": f"/runs/{item['run_id']}#span-{item['span_id']}",
    }