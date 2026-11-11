"""Checkpoint 2 Early-Billing & Post-Enrollment Claims Pipeline.

Monitors providers during their first 6-12 months of billing:
- Hard billing edits (hours > 24, hospital inpatient overlaps, post-mortem claims)
- Billing ramp anomaly detection against peer cohorts
- Patient cohort migration from suspended or terminated providers
- Emits post-enrollment risk signals back into CPMM Checkpoint 1 maintenance events
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..claims.billing_rules import evaluate_hard_billing_edits
from ..claims.patient_sharing import PatientMigrationDetector
from ..claims.ramp_detector import BillingRampDetector
from ..config import SystemConfig
from ..models.entities import ClaimRecord, ProviderApplication
from ..models.results import EvidenceItem, EvidenceSeverity, RampAnomalyResult


@dataclass
class Checkpoint2SignalPackage:
    """Post-enrollment risk signal emitted from MMIS/claims monitoring."""
    provider_npi: str
    months_enrolled: int
    ramp_result: RampAnomalyResult
    patient_migration_evidence: List[EvidenceItem] = field(default_factory=list)
    total_evidence_items: List[EvidenceItem] = field(default_factory=list)
    recommended_cpmm_action: str = "CONTINUE_STANDARD_MONITORING"
    trigger_unannounced_site_visit: bool = False
    trigger_payment_suspension: bool = False


class Checkpoint2Pipeline:
    """Claims surveillance and post-enrollment anomaly detector."""

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or SystemConfig()
        self.ramp_detector = BillingRampDetector(self.config.claims)
        self.migration_detector = PatientMigrationDetector(self.config.claims)

    def register_sanctioned_provider(self, npi: str):
        """Register a known bad or suspended provider NPI for patient migration tracking."""
        self.migration_detector.register_sanctioned_provider(npi)

    def ingest_claims(self, claims: List[ClaimRecord]):
        """Ingest claims for bipartite network tracking."""
        self.migration_detector.ingest_claims(claims)

    def evaluate_provider_early_billing(
        self,
        app: ProviderApplication,
        months_enrolled: int,
        claims: List[ClaimRecord]
    ) -> Checkpoint2SignalPackage:
        """Run complete Checkpoint 2 claims intelligence on a provider."""
        # 1. Ingest claims into bipartite patient graph
        self.ingest_claims(claims)

        # 2. Evaluate Billing Ramp and Hard MMIS Edits
        ramp_res = self.ramp_detector.evaluate_provider_claims(
            provider_npi=app.npi,
            service_category=app.service_category,
            months_enrolled=months_enrolled,
            claims=claims
        )

        # 3. Evaluate Patient Cohort Migration
        migration_evidence = self.migration_detector.evaluate_patient_migration(app.npi)

        # 4. Synthesize all evidence
        all_evidence: List[EvidenceItem] = list(ramp_res.evidence_items) + list(migration_evidence)

        # 5. Determine recommended enrollment action
        has_critical = any(e.severity == EvidenceSeverity.CRITICAL for e in all_evidence)
        trigger_site_visit = False
        trigger_suspension = False
        action = "CONTINUE_STANDARD_MONITORING"

        if ramp_res.impossible_hours_count > 0 or ramp_res.deceased_overlap_count > 0 or len(migration_evidence) > 0:
            action = "CREDIBLE_ALLEGATION_OF_FRAUD_REFERRAL (42 CFR 455.23 Payment Suspension Evaluation)"
            trigger_suspension = True
            trigger_site_visit = True
        elif ramp_res.is_ramp_anomaly:
            action = "OFF_CYCLE_REVALIDATION_AND_UNANNOUNCED_SITE_VISIT"
            trigger_site_visit = True
        elif len(all_evidence) > 0:
            action = "ENHANCED_OVERSIGHT_DESK_AUDIT"

        return Checkpoint2SignalPackage(
            provider_npi=app.npi,
            months_enrolled=months_enrolled,
            ramp_result=ramp_res,
            patient_migration_evidence=migration_evidence,
            total_evidence_items=all_evidence,
            recommended_cpmm_action=action,
            trigger_unannounced_site_visit=trigger_site_visit,
            trigger_payment_suspension=trigger_suspension
        )
