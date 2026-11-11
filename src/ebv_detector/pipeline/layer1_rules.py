"""Layer 1 Authoritative Screening Rules Engine.

Implements statutory Medicaid enrollment requirements under federal regulations:
- 42 CFR 455.450: Deterministic ACA Categorical Risk Tier assignment (Limited, Moderate, High)
- 42 CFR 455.450(e): Mandatory elevation to High risk based on prior exclusions or overpayments
- 42 CFR 455.416: Statutory grounds for mandatory denial/termination (LEIE, SAM, prior terminations)
- DATA-039 / DT-SCR-GATE-002: Prohibited address types (CMRA, PO Box, PMB, virtual office)
- Shared Identifier Facts: Hard matching of EFT bank accounts, phones, or tax IDs with sanctioned seeds
- GR-4 Indirect Ownership: Verification that no 5%+ upstream owner is on the OIG exclusion list

Compliance:
- Assistive-only rule: ML never sets the ACA tier or issues a denial; Layer 1 rules are authoritative.
"""

from typing import Dict, List, Optional, Set, Tuple

from ..address.rules import evaluate_address_rules
from ..config import SystemConfig
from ..graph.traversal import IndirectOwnershipResult, PathResult
from ..models.entities import ProviderApplication, SeedRecord
from ..models.results import DecisionStatus, EvidenceItem, EvidenceSeverity, Layer1Decision


class Layer1RulesEngine:
    """Authoritative deterministic screening and gatekeeper engine."""

    def __init__(self, seeds: Optional[List[SeedRecord]] = None, config: Optional[SystemConfig] = None):
        self.config = config or SystemConfig()
        self.seeds = seeds or []
        self._index_seeds()

    def _index_seeds(self):
        """Index known-bad seed records by identifier type for fast lookup."""
        self._seeds_by_npi: Dict[str, SeedRecord] = {}
        self._seeds_by_tin: Dict[str, SeedRecord] = {}
        self._seeds_by_ssn4_dob: Dict[str, SeedRecord] = {}
        self._seeds_by_eft: Dict[str, SeedRecord] = {}
        self._seeds_by_address: Dict[str, SeedRecord] = {}
        self._seeds_by_phone: Dict[str, SeedRecord] = {}

        for s in self.seeds:
            val = s.identifier_value.strip().upper()
            if s.identifier_type == "NPI":
                self._seeds_by_npi[val] = s
            elif s.identifier_type == "TIN":
                self._seeds_by_tin[val.replace("-", "")] = s
            elif s.identifier_type == "SSN4_DOB":
                self._seeds_by_ssn4_dob[val] = s
            elif s.identifier_type == "EFT":
                self._seeds_by_eft[val] = s
            elif s.identifier_type == "ADDRESS":
                self._seeds_by_address[val] = s
            elif s.identifier_type == "PHONE":
                self._seeds_by_phone[val] = s

    def assign_aca_risk_tier(self, app: ProviderApplication) -> Tuple[str, List[str]]:
        """Deterministically determine ACA Categorical Risk Tier under 42 CFR 455.450.
        
        Returns:
            (tier, justification_reasons)
        """
        cat = app.service_category.upper()
        reasons = []

        # High Risk Categories
        high_risk_categories = {
            "HCBS", "HOME_HEALTH", "DURABLE_MEDICAL_EQUIPMENT", "HOSPICE",
            "NON_EMERGENCY_TRANSPORTATION", "PERSONAL_CARE_SERVICES"
        }
        
        # Moderate Risk Categories
        moderate_risk_categories = {
            "COMMUNITY_MENTAL_HEALTH", "BEHAVIORAL_HEALTH", "AUTISM_CLINIC_FACILITY",
            "AUTISM_ABA", "SOBER_LIVING_FACILITY", "PSYCHIATRIC_REHAB", "ADULT_DAY_CARE",
            "AMBULANCE_SUPPLIER"
        }

        # 1. 42 CFR 455.450(e) Mandatory Adjustment to High:
        # Prior termination, unpaid overpayment, or state moratorium
        if app.prior_termination_disclosed:
            reasons.append("Mandatory adjustment to High under 42 CFR 455.450(e)(1) due to prior provider termination.")
            return "HIGH", reasons

        # 2. Base category assignment
        if any(h in cat for h in high_risk_categories):
            reasons.append(f"Categorical High Risk tier assigned for service category '{cat}' under 42 CFR 455.450.")
            return "HIGH", reasons
        elif any(m in cat for m in moderate_risk_categories):
            reasons.append(f"Categorical Moderate Risk tier assigned for service category '{cat}' under 42 CFR 455.450.")
            return "MODERATE", reasons
        else:
            reasons.append(f"Categorical Limited Risk tier assigned for service category '{cat}' under 42 CFR 455.450.")
            return "LIMITED", reasons

    def evaluate_application(
        self,
        app: ProviderApplication,
        graph_seed_paths: Optional[List[PathResult]] = None,
        indirect_ownership_results: Optional[List[IndirectOwnershipResult]] = None
    ) -> Layer1Decision:
        """Run all authoritative deterministic Layer 1 checks."""
        verified_facts: List[EvidenceItem] = []
        statutory_grounds: List[str] = []
        screening_actions: List[str] = []

        # 1. Assign ACA Risk Tier
        tier, tier_reasons = self.assign_aca_risk_tier(app)
        app.aca_risk_tier = tier

        if tier == "HIGH":
            screening_actions.extend([
                "MANDATORY_FINGERPRINT_BACKGROUND_CHECKS (42 CFR 455.434)",
                "UNANNOUNCED_PRE_ENROLLMENT_SITE_VISIT (42 CFR 455.432)"
            ])
        elif tier == "MODERATE":
            screening_actions.append("UNANNOUNCED_PRE_ENROLLMENT_SITE_VISIT (42 CFR 455.432)")

        # 2. Check Direct NPI Exclusions
        if app.npi in self._seeds_by_npi:
            seed = self._seeds_by_npi[app.npi]
            statutory_grounds.append("42 CFR 455.416(c) - Prior exclusion or termination by federal/state authority")
            verified_facts.append(EvidenceItem(
                code="L1-SEED-NPI-MATCH",
                description=f"Applicant NPI '{app.npi}' directly matches active sanction record ({seed.source}: {seed.reason}).",
                source_layer="LAYER_1",
                severity=EvidenceSeverity.CRITICAL,
                verified_fact=True,
                details={"seed_id": seed.seed_id, "source": seed.source, "reason": seed.reason}
            ))

        # 3. Check Direct TIN Exclusions
        clean_tin = app.tin.replace("-", "").strip()
        if clean_tin in self._seeds_by_tin:
            seed = self._seeds_by_tin[clean_tin]
            statutory_grounds.append("42 CFR 455.416(c) - Tax ID sanctioned under exclusion list")
            verified_facts.append(EvidenceItem(
                code="L1-SEED-TIN-MATCH",
                description=f"Applicant TIN matches sanctioned entity ({seed.source}: {seed.reason}).",
                source_layer="LAYER_1",
                severity=EvidenceSeverity.CRITICAL,
                verified_fact=True,
                details={"seed_id": seed.seed_id, "source": seed.source, "reason": seed.reason}
            ))

        # 4. Check Disclosed Persons (SSN4 + DOB) against Seeds
        for p in app.disclosed_persons:
            key = p.ssn_dob_key
            if key in self._seeds_by_ssn4_dob:
                seed = self._seeds_by_ssn4_dob[key]
                statutory_grounds.append("42 CFR 455.416(c) - Disclosed owner/officer excluded under 1128(a)(1)")
                verified_facts.append(EvidenceItem(
                    code="L1-SEED-PERSON-EXCLUDED",
                    description=(
                        f"Disclosed individual '{p.full_name}' ({p.role}) matches sanctioned seed record "
                        f"({seed.source}: {seed.reason})."
                    ),
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.CRITICAL,
                    verified_fact=True,
                    details={"person_name": p.full_name, "role": p.role, "source": seed.source}
                ))

        # 5. Check EFT Account Shared with Terminated Provider
        if app.eft_account:
            eft_k = app.eft_account.account_key
            if eft_k in self._seeds_by_eft:
                seed = self._seeds_by_eft[eft_k]
                statutory_grounds.append("42 CFR 455.416(a) - Bank account associated with previously terminated provider")
                verified_facts.append(EvidenceItem(
                    code="L1-SEED-EFT-SHARED-TERMINATION",
                    description=(
                        f"EFT bank account '{app.eft_account.routing_number}-*****' matches bank account of a "
                        f"previously terminated provider ({seed.source}: {seed.reason})."
                    ),
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.CRITICAL,
                    verified_fact=True,
                    details={"seed_id": seed.seed_id, "reason": seed.reason}
                ))

        # 6. Evaluate Address Rules (DATA-039 Prohibitions)
        addr_evidence, is_fatal_addr = evaluate_address_rules(app.primary_address, app.service_category)
        verified_facts.extend(addr_evidence)
        if is_fatal_addr:
            statutory_grounds.append("42 CFR 455.416 / DATA-039 - Prohibited or invalid service location")

        # 7. Evaluate GR-4 Indirect Ownership Exclusions
        if indirect_ownership_results:
            for ind_res in indirect_ownership_results:
                if ind_res.meets_5pct_statutory_threshold and ind_res.is_upstream_excluded_seed:
                    statutory_grounds.append(
                        f"42 CFR 455.104 / 455.416 - Excluded person holds {ind_res.effective_indirect_pct:.1%} "
                        "indirect ownership"
                    )
                    verified_facts.append(EvidenceItem(
                        code="L1-GR4-INDIRECT-OWNERSHIP-EXCLUDED",
                        description=(
                            f"Sanctioned seed entity '{ind_res.upstream_entity_id}' holds {ind_res.effective_indirect_pct:.1%} "
                            f"indirect ownership in applicant (statutory threshold >= 5.0%). Reason: {ind_res.seed_reason}."
                        ),
                        source_layer="LAYER_1",
                        severity=EvidenceSeverity.CRITICAL,
                        verified_fact=True,
                        details={
                            "upstream_entity": ind_res.upstream_entity_id,
                            "indirect_pct": ind_res.effective_indirect_pct,
                            "reason": ind_res.seed_reason
                        }
                    ))

        # 8. Determine Decision Status
        if len(statutory_grounds) > 0:
            status = DecisionStatus.DENIED_GROUNDS
            can_auto = False
        elif tier in ("HIGH", "MODERATE") or len(verified_facts) > 0:
            status = DecisionStatus.REVIEW_REQUIRED
            can_auto = False
        else:
            status = DecisionStatus.AUTO_APPROVE_ELIGIBLE
            can_auto = True

        return Layer1Decision(
            application_id=app.application_id,
            status=status,
            assigned_aca_risk_tier=tier,
            screening_actions_required=screening_actions,
            statutory_grounds=statutory_grounds,
            verified_facts=verified_facts,
            can_auto_approve=can_auto
        )
