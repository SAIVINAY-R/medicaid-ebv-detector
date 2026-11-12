"""Graph Node and Edge schema definitions with temporal validity for M3 Relationship Graph."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class NodeType(str, Enum):
    PROVIDER = "PROVIDER"
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    ADDRESS = "ADDRESS"
    EFT_ACCOUNT = "EFT_ACCOUNT"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    DEVICE = "DEVICE"
    SEED = "SEED"


class EdgeType(str, Enum):
    DIRECT_OWNERSHIP = "DIRECT_OWNERSHIP"     # Person or Org owns Child Org (carries ownership_pct)
    MANAGING_EMPLOYEE = "MANAGING_EMPLOYEE"   # Person manages Provider
    AUTHORIZED_OFFICIAL = "AUTHORIZED_OFFICIAL"
    CLINICIAN = "CLINICIAN"                   # Rendering or supervising clinician
    USES_ADDRESS = "USES_ADDRESS"
    USES_EFT = "USES_EFT"
    USES_PHONE = "USES_PHONE"
    USES_EMAIL = "USES_EMAIL"
    SUBMITTED_FROM = "SUBMITTED_FROM"         # Device ID or IP
    LINKED_SEED = "LINKED_SEED"               # Directly linked to an exclusion / CAF seed


@dataclass
class GraphNode:
    """A node in the relationship intelligence graph."""
    node_id: str
    node_type: NodeType
    label: str
    is_seed: bool = False
    seed_source: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """A directed, weighted, temporally bounded edge in the relationship graph."""
    source_id: str
    target_id: str
    edge_type: EdgeType
    weight: float = 1.0
    ownership_percentage: float = 0.0 # Used for GR-4 indirect ownership calculation
    effective_start: str = "2020-01-01"
    effective_end: Optional[str] = None

