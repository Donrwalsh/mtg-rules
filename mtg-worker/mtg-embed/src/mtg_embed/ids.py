from __future__ import annotations

import uuid

# Fixed, arbitrary namespace so point IDs stay stable across processes and
# runs. The exact value doesn't matter -- it just must never change once
# points exist in Qdrant, or every re-run would look like a fresh corpus.
_NAMESPACE = uuid.UUID("6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a")


def rule_point_id(rule_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"rule:{rule_id}"))


def oracle_point_id(oracle_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"oracle:{oracle_id}"))


def ruling_point_id(oracle_id: str, index: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"ruling:{oracle_id}:{index}"))
