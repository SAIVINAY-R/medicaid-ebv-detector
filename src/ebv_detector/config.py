"""Configuration parameters, thresholds, and regulatory anchors for the EBV Fraud Detection System.

Complies with:
- ADR-0001 (Dataverse + Azure Function rules, Azure ML assistive, human adverse determinations)
- 42 CFR 455.104-106 (Disclosures of ownership, business transactions, convictions)
- 42 CFR 455.416 (Adverse action grounds: failed site visit, falsified info, prior exclusions)
- 42 CFR 455.450 (Deterministic ACA risk tiers)
- NC RFP #30-2026-017-DHB Attachment C Section 3 (Assistive only, no state data training)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass(frozen=True)
class EntityResolutionConfig:
    """M1 Fellegi-Sunter and near-match queue thresholds."""
    # Weight thresholds (log2 likelihood ratio score)
    match_threshold: float = 12.0       # Auto-link threshold (advisory/high confidence)
    near_match_threshold: float = 5.0   # Near-match queue threshold (sent to human reviewer)
    non_match_threshold: float = 0.0    # Definite non-match below this
    
    # String comparator cutoffs
    jaro_winkler_high: float = 0.92
    jaro_winkler_med: float = 0.82
    levenshtein_max_dist: int = 2
    
    # Blocking passes
    blocking_keys: List[str] = field(default_factory=lambda: [
        "npi",
        "tin",
        "ssn_last4_dob",
        "medicaid_id",
        "phone_clean",
        "eft_routing_account",
        "address_norm_zip"
    ])


@dataclass(frozen=True)
class GraphConfig:
    """M3 Deterministic traversal and risk propagation configuration."""
    max_traversal_hops: int = 3
    indirect_ownership_threshold: float = 0.05  # 5% threshold under 42 CFR 455.104
    
    # Supernode damping
    supernode_degree_threshold: int = 30  # Nodes with >30 links (registered agents, large hospital campuses)
    supernode_damping_factor: float = 0.1 # Heavily down-weight edge weight through supernodes
    
    # Personalized PageRank (PPR)
    ppr_restart_prob: float = 0.15        # Alpha: probability of restarting at bad seeds
    ppr_max_iterations: int = 50
    ppr_tolerance: float = 1e-6
    ppr_alert_threshold: float = 0.05     # Advisory alert threshold for seed proximity


@dataclass(frozen=True)
class AddressConfig:
    """M2 Address risk parameters and DATA-039 prohibitions."""
    # High-risk address types prohibited or flagged under DATA-039 / DT-SCR-GATE-002
    prohibited_types: Set[str] = field(default_factory=lambda: {
        "CMRA",             # Commercial Mail Receiving Agency (e.g. UPS Store, Mailboxes Etc.)
        "PO_BOX",           # Post Office Box
        "VIRTUAL_MAILBOX",  # Digital/virtual mailboxes (e.g. Earth Class Mail, iPostal1)
        "PMB"               # Private Mailbox
    })
    
    # Facility-type services that cannot legitimately operate from residential addresses
    facility_service_types: Set[str] = field(default_factory=lambda: {
        "ADULT_DAY_CARE",
        "PSYCHIATRIC_REHAB",
        "INTENSIVE_OUTPATIENT",
        "SOBER_LIVING_FACILITY",
        "AUTISM_CLINIC_FACILITY",
        "CRISIS_STABILIZATION_CENTER"
    })
    
    # High-density clustering threshold (number of unrelated providers at single address)
    unrelated_provider_density_threshold: int = 4
    
    # Legitimate co-location allowlist (exempt from density alarms)
    allowlisted_facility_types: Set[str] = field(default_factory=lambda: {
        "FQHC",             # Federally Qualified Health Center
        "CHC",              # Community Health Center
        "TRIBAL_HEALTH",    # Tribal Health Center / IHS
        "HOSPITAL_CAMPUS",  # Academic Medical Center / Hospital Campus
        "ACCREDITED_MEDICAL_PLAZA"
    })


@dataclass(frozen=True)
class ApplicationAnomalyConfig:
    """M4 Submission velocity and MinHash template detection."""
    # Submission velocity thresholds
    max_submissions_per_ip_7days: int = 3
    max_submissions_per_device_7days: int = 2
    max_submissions_per_email_30days: int = 3
    
    # MinHash LSH template similarity
    minhash_num_perm: int = 128
    template_similarity_threshold: float = 0.80  # Jaccard similarity indicating templated narrative/manual


@dataclass(frozen=True)
class ClaimsSignalConfig:
    """Checkpoint 2 billing ramp and claims anomaly thresholds."""
    # Ramp velocity (ratio vs peer cohort median in first 6-12 months)
    ramp_robust_z_threshold: float = 3.5
    min_paid_amount_for_ramp_alert: float = 250_000.0  # Dollars
    
    # Hard billing edit limits
    max_hours_per_staff_per_day: float = 24.0
    
    # Patient cohort migration (bipartite graph Jaccard overlap)
    patient_migration_jaccard_threshold: float = 0.35
    patient_migration_min_count: int = 10


@dataclass(frozen=True)
class SystemConfig:
    """Overall EBV system configuration combining all checkpoints."""
    entity_resolution: EntityResolutionConfig = field(default_factory=EntityResolutionConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    address: AddressConfig = field(default_factory=AddressConfig)
    anomaly: ApplicationAnomalyConfig = field(default_factory=ApplicationAnomalyConfig)
    claims: ClaimsSignalConfig = field(default_factory=ClaimsSignalConfig)
    
    # System metadata
    model_version: str = "v1.0.0-phase0"
    wORM_audit_enabled: bool = True

