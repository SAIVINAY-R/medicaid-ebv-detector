"""Result types, evidence panels, decision statuses, and audit records."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EvidenceSeverity(str, Enum):
    CRITICAL = "CRITICAL"  # Statutory or direct exclusion link (immediate referral)
    HIGH = "HIGH"          # Multiple strong fraud indicators (site visit / escalation)
    MEDIUM = "MEDIUM"      # Single anomaly or near-match requiring clarification
    LOW = "LOW"            # Advisory context
    INFO = "INFO"          # Informational metadata


class DecisionStatus(str, Enum):
    AUTO_APPROVE_ELIGIBLE = "AUTO_APPROVE_ELIGIBLE" # Passes all Layer 1 rules cleanly
    REVIEW_REQUIRED = "REVIEW_REQUIRED"             # Human reviewer must inspect
    REFERRED_TO_PI = "REFERRED_TO_PI"               # Escalated to Program Integrity
    GROUNDS_PENDING_HUMAN_DECISION = "GROUNDS_PENDING_HUMAN_DECISION" # Statutory ground flagged pending human adverse action
    DENIED_GROUNDS = "DENIED_GROUNDS"               # Human confirmed 42 CFR 455.416 grounds


@dataclass
class EvidenceItem:
    """An individual piece of explainable evidence surfaced to the reviewer."""
    code: str                         # e.g. "L1-SEED-EFT", "M3-PROXIMITY-EXCLUSION", "M2-CMRA-PROHIBITED"
    description: str
    source_layer: str                 # "LAYER_1", "M1_RESOLVER", "M2_ADDRESS", "M3_GRAPH", "M4_ANOMALY", "CHECKPOINT_2_CLAIMS"
    severity: EvidenceSeverity
    verified_fact: bool               # True if deterministic verified fact; False if statistical advisory
    fact_key: Optional[str] = None    # Canonical fact identifier to collapse duplicate evidence items
    family: Optional[str] = None      # Evidence family: "AFFILIATION", "OWNERSHIP", "ADDRESS", "SUBMISSION", "IDENTITY", "AGE"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "description": self.description,
            "source_layer": self.source_layer,
            "severity": self.severity.value,
            "verified_fact": self.verified_fact,
            "fact_key": self.fact_key,
            "family": self.family,
            "details": self.details,
        }


@dataclass
class MatchResult:
    """M1 Entity resolution match output."""
    candidate_id_a: str
    candidate_id_b: str
    match_score: float                # Fellegi-Sunter log2 likelihood ratio weight
    posterior_prob: float             # Calibrated posterior probability of match
    is_match: bool                    # Above match threshold
    is_near_match: bool               # In near-match human adjudication range
    blocking_key_matched: str
    field_level_comparisons: Dict[str, float] = field(default_factory=dict)
    recommended_action: str = "NO_ACTION" # "AUTO_LINK", "SEND_TO_QUEUE", "DO_NOT_LINK"
    num_fields_compared: int = 0
    hard_conflict: bool = False
    explanation: str = ""


@dataclass
class RampAnomalyResult:
    """Checkpoint 2 billing ramp anomaly output."""
    provider_npi: str
    service_category: str
    months_enrolled: int
    total_paid_30d: float
    cohort_median_paid_30d: float
    cohort_mad_paid_30d: float
    robust_z_score: float
    is_ramp_anomaly: bool
    cusum_alarm: bool
    impossible_hours_count: int
    inpatient_overlap_count: int
    deceased_overlap_count: int
    evidence_items: List[EvidenceItem] = field(default_factory=list)


@dataclass
class Layer1Decision:
    """Deterministic Layer 1 gate decision (authoritative per ADR-0001)."""
    application_id: str
    status: DecisionStatus
    assigned_aca_risk_tier: str       # "LIMITED", "MODERATE", "HIGH" (42 CFR 455.450)
    screening_actions_required: List[str] = field(default_factory=list)
    statutory_grounds: List[str] = field(default_factory=list) # e.g. "42 CFR 455.416(c)"
    verified_facts: List[EvidenceItem] = field(default_factory=list)
    can_auto_approve: bool = False

    @property
    def has_adverse_triggers(self) -> bool:
        return len(self.statutory_grounds) > 0 or self.status in {
            DecisionStatus.REVIEW_REQUIRED,
            DecisionStatus.REFERRED_TO_PI,
            DecisionStatus.GROUNDS_PENDING_HUMAN_DECISION,
            DecisionStatus.DENIED_GROUNDS
        }


@dataclass
class AdvisoryScore:
    """Layer 2 assistive ML scoring and reviewer evidence package."""
    application_id: str
    priority_score: float             # 0.0 to 100.0 (queue ordering only)
    composite_risk_level: str         # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    evidence_items: List[EvidenceItem] = field(default_factory=list)
    recommended_review_actions: List[str] = field(default_factory=list) # "UNANNOUNCED_SITE_VISIT", "SUBMIT_FINGERPRINTS"
    explanation_summary: str = ""
    feature_contributions: Dict[str, float] = field(default_factory=dict)


@dataclass
class AuditRecord:
    """WORM-compliant immutable audit record for CPMM decisions (ADR-0001)."""
    record_id: str
    application_id: str
    timestamp_utc: str
    feature_vector_sha256: str
    model_version: str
    snapshot_id: str
    layer1_status: str
    advisory_priority_score: float
    verified_fact_codes: List[str]
    event_type: str = "SYSTEM_INFERENCE" # "SYSTEM_INFERENCE" or "REVIEWER_DECISION"
    parent_record_id: Optional[str] = None
    config_weights_sha256: str = ""
    reviewer_id: Optional[str] = None
    reviewer_action: Optional[str] = None # "APPROVED", "DENIED", "REFERRED", "VOIDED"
    reviewer_notes: Optional[str] = None
    audit_hash: str = ""              # Chained SHA-256 hash of this record

