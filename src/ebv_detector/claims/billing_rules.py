"""Checkpoint 2 Hard Billing Rules and Statutory MMIS Edits.

Enforces deterministic program integrity edits on claims:
- Impossible Hours: Billing > 24 hours of service in a single calendar day per clinician
- Inpatient Overlap: Billing outpatient community services while beneficiary is hospitalized
- Post-Mortem Claims: Billing services rendered after beneficiary date of death
"""

from collections import defaultdict
from typing import Dict, List, Tuple

from ..config import ClaimsSignalConfig
from ..models.entities import ClaimRecord
from ..models.results import EvidenceItem, EvidenceSeverity


def evaluate_hard_billing_edits(
    claims: List[ClaimRecord],
    config: Optional[ClaimsSignalConfig] = None
) -> Tuple[List[EvidenceItem], int, int, int]:
    """Evaluate MMIS claims against hard statutory and physical billing edits.
    
    Returns:
        (evidence_items, impossible_hours_count, inpatient_overlap_count, deceased_overlap_count)
    """
    cfg = config or ClaimsSignalConfig()
    evidence: List[EvidenceItem] = []
    
    # Track hours billed per (rendering_clinician, service_date)
    clinician_daily_hours: Dict[Tuple[str, str], float] = defaultdict(float)
    inpatient_violations = 0
    deceased_violations = 0
    impossible_hours_violations = 0

    for c in claims:
        # Check clinician daily hours
        key = (c.rendering_clinician_npi, c.service_date)
        clinician_daily_hours[key] += c.service_hours

        # Check inpatient overlap
        if c.inpatient_overlap:
            inpatient_violations += 1

        # Check post-mortem
        if c.beneficiary_deceased:
            deceased_violations += 1

    # Evaluate impossible hours
    violating_clinicians = set()
    for (clinician_npi, s_date), total_hours in clinician_daily_hours.items():
        if total_hours > cfg.max_hours_per_staff_per_day:
            impossible_hours_violations += 1
            violating_clinicians.add((clinician_npi, s_date, total_hours))

    if impossible_hours_violations > 0:
        sample = list(violating_clinicians)[:3]
        sample_desc = ", ".join([f"{c} on {d} ({h:.1f}h)" for c, d, h in sample])
        evidence.append(EvidenceItem(
            code="CP2-BILLING-IMPOSSIBLE-HOURS",
            description=(
                f"Detected {impossible_hours_violations} clinician-days exceeding 24 hours of service in a single day. "
                f"Sample: {sample_desc}. Strong indicator of fabricated billing."
            ),
            source_layer="CHECKPOINT_2_CLAIMS",
            severity=EvidenceSeverity.CRITICAL,
            verified_fact=True,
            details={"violation_count": impossible_hours_violations, "sample_clinicians": [c for c, _, _ in sample]}
        ))

    if inpatient_violations > 0:
        evidence.append(EvidenceItem(
            code="CP2-BILLING-INPATIENT-OVERLAP",
            description=(
                f"Detected {inpatient_violations} claims billed for community services while beneficiary was an active "
                "hospital inpatient. Physical impossibility."
            ),
            source_layer="CHECKPOINT_2_CLAIMS",
            severity=EvidenceSeverity.CRITICAL,
            verified_fact=True,
            details={"inpatient_violation_count": inpatient_violations}
        ))

    if deceased_violations > 0:
        evidence.append(EvidenceItem(
            code="CP2-BILLING-POST-MORTEM",
            description=(
                f"Detected {deceased_violations} claims billed with service dates following beneficiary date of death. "
                "Ghost billing confirmed."
            ),
            source_layer="CHECKPOINT_2_CLAIMS",
            severity=EvidenceSeverity.CRITICAL,
            verified_fact=True,
            details={"deceased_violation_count": deceased_violations}
        ))

    return evidence, impossible_hours_violations, inpatient_violations, deceased_violations
