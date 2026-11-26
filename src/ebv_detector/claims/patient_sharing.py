"""Checkpoint 2 Patient-Sharing Bipartite Network & Cohort Migration Detector.

Analyzes bipartite provider-beneficiary relationship graphs to detect:
- Coordinated migration of patient cohorts from suspended/terminated providers to newly enrolled sham entities
- Patient recruitment rings where beneficiaries are traded or moved in blocks
- High Jaccard beneficiary overlap with known-bad seed providers
"""

from collections import defaultdict
from typing import Dict, List, Optional, Set

from ..config import ClaimsSignalConfig
from ..models.entities import ClaimRecord
from ..models.results import EvidenceItem, EvidenceSeverity


class PatientMigrationDetector:
    """Monitors bipartite provider-beneficiary graphs for suspicious cohort transfers."""

    def __init__(self, config: Optional[ClaimsSignalConfig] = None):
        self.config = config or ClaimsSignalConfig()
        # provider_npi -> set of beneficiary_ids
        self._provider_patients: Dict[str, Set[str]] = defaultdict(set)
        # provider_npi -> (min_service_date, max_service_date)
        self._provider_date_bounds: Dict[str, List[str]] = {}
        # known suspended or terminated provider NPIs
        self._sanctioned_providers: Set[str] = set()

    def register_sanctioned_provider(self, npi: str):
        """Mark a provider NPI as suspended, terminated, or under CAF hold."""
        self._sanctioned_providers.add(npi)

    def ingest_claims(self, claims: List[ClaimRecord]):
        """Populate provider-patient bipartite associations from claims data."""
        for c in claims:
            self._provider_patients[c.billing_provider_npi].add(c.beneficiary_id)
            if c.service_date:
                bounds = self._provider_date_bounds.get(c.billing_provider_npi)
                if not bounds:
                    self._provider_date_bounds[c.billing_provider_npi] = [c.service_date, c.service_date]
                else:
                    if c.service_date < bounds[0]:
                        bounds[0] = c.service_date
                    if c.service_date > bounds[1]:
                        bounds[1] = c.service_date

    def evaluate_patient_migration(self, target_provider_npi: str) -> List[EvidenceItem]:
        """Check if target provider's patients were transferred en masse from a sanctioned provider."""
        target_patients = self._provider_patients.get(target_provider_npi, set())
        if not target_patients:
            return []

        evidence: List[EvidenceItem] = []
        target_bounds = self._provider_date_bounds.get(target_provider_npi)

        for sanctioned_npi in self._sanctioned_providers:
            if sanctioned_npi == target_provider_npi:
                continue

            sanctioned_patients = self._provider_patients.get(sanctioned_npi, set())
            if not sanctioned_patients:
                continue

            # Require plausible migration ordering when dates are available
            # (sanctioned provider's earliest claim must not strictly post-date target's latest claim)
            sanc_bounds = self._provider_date_bounds.get(sanctioned_npi)
            if sanc_bounds and target_bounds and sanc_bounds[0] > target_bounds[1]:
                continue

            shared_patients = target_patients & sanctioned_patients
            total_unique = target_patients | sanctioned_patients
            jaccard = len(shared_patients) / len(total_unique) if total_unique else 0.0

            # Trigger condition: high Jaccard OR large absolute count of patients transferred
            if (
                jaccard >= self.config.patient_migration_jaccard_threshold
                or len(shared_patients) >= self.config.patient_migration_min_count
            ):
                pct_of_target = (len(shared_patients) / len(target_patients)) * 100.0
                evidence.append(EvidenceItem(
                    code="CP2-PATIENT-COHORT-MIGRATION",
                    description=(
                        f"{len(shared_patients)} patients ({pct_of_target:.1f}% of active patient roster) were transferred "
                        f"directly from suspended/terminated provider '{sanctioned_npi}'. "
                        f"Bipartite Jaccard similarity: {jaccard:.2f}. Kickback/recruiter migration pattern."
                    ),
                    source_layer="CHECKPOINT_2_CLAIMS",
                    severity=EvidenceSeverity.CRITICAL,
                    verified_fact=False,
                    fact_key=f"MIGRATION:{sanctioned_npi}",
                    family="AFFILIATION",
                    details={
                        "sanctioned_provider_npi": sanctioned_npi,
                        "shared_patient_count": len(shared_patients),
                        "total_target_patients": len(target_patients),
                        "jaccard_similarity": round(jaccard, 3),
                        "threshold_jaccard": self.config.patient_migration_jaccard_threshold,
                        "threshold_count": self.config.patient_migration_min_count,
                        "sample_patient_ids": list(shared_patients)[:5]
                    }
                ))

        return evidence

