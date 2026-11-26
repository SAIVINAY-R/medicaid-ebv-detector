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

from collections import defaultdict
import re
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
        """Index known-bad seed records by normalized identifier type without overwriting duplicates."""
        self._seeds_by_npi: Dict[str, List[SeedRecord]] = defaultdict(list)
        self._seeds_by_tin: Dict[str, List[SeedRecord]] = defaultdict(list)
        self._seeds_by_ssn4_dob: Dict[str, List[SeedRecord]] = defaultdict(list)
        self._seeds_by_eft: Dict[str, List[SeedRecord]] = defaultdict(list)
        self._seeds_by_address: Dict[str, List[SeedRecord]] = defaultdict(list)
        self._seeds_by_phone: Dict[str, List[SeedRecord]] = defaultdict(list)

        for s in self.seeds:
            val = s.identifier_value.strip().upper()
            if s.identifier_type == "NPI":
                self._seeds_by_npi[val].append(s)
            elif s.identifier_type == "TIN":
                clean_tin = re.sub(r"\D", "", val) or val.replace("-", "")
                self._seeds_by_tin[clean_tin].append(s)
            elif s.identifier_type == "SSN4_DOB":
                self._seeds_by_ssn4_dob[val].append(s)
            elif s.identifier_type == "EFT":
                self._seeds_by_eft[val].append(s)
            elif s.identifier_type == "ADDRESS":
                self._seeds_by_address[val].append(s)
            elif s.identifier_type == "PHONE":
                clean_p = "".join(filter(str.isdigit, val))
                if clean_p:
                    self._seeds_by_phone[clean_p].append(s)
                if val != clean_p:
                    self._seeds_by_phone[val].append(s)

    def _find_active_seed(self, seed_list: Optional[List[SeedRecord]], app_date: str) -> Optional[SeedRecord]:
        """Return first seed in list that is active on app_date."""
        if not seed_list:
            return None
        for s in seed_list:
            if s.is_active_on(app_date):
                return s
        return None

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

        # 2. Check Direct NPI Exclusions & Prior Enrolled NPI Exclusions
        norm_npi = (app.npi or "").strip().upper()
        npi_seed = self._find_active_seed(self._seeds_by_npi.get(norm_npi), app.application_date)
        if npi_seed:
            statutory_grounds.append("42 CFR 455.416(c) - Prior exclusion or termination by federal/state authority")
            verified_facts.append(EvidenceItem(
                code="L1-SEED-NPI-MATCH",
                description=f"Applicant NPI '{norm_npi}' directly matches active sanction record ({npi_seed.source}: {npi_seed.reason}).",
                source_layer="LAYER_1",
                severity=EvidenceSeverity.CRITICAL,
                verified_fact=True,
                fact_key=f"NPI:{norm_npi}",
                family="AFFILIATION",
                details={"seed_id": npi_seed.seed_id, "source": npi_seed.source, "reason": npi_seed.reason}
            ))

        if app.prior_enrolled_npi:
            prior_npi = app.prior_enrolled_npi.strip().upper()
            prior_seed = self._find_active_seed(self._seeds_by_npi.get(prior_npi), app.application_date)
            if prior_seed:
                statutory_grounds.append("42 CFR 455.416(c) - Prior enrolled NPI sanctioned under exclusion list")
                verified_facts.append(EvidenceItem(
                    code="L1-SEED-PRIOR-NPI-MATCH",
                    description=f"Prior enrolled NPI '{prior_npi}' matches active sanction record ({prior_seed.source}: {prior_seed.reason}).",
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.CRITICAL,
                    verified_fact=True,
                    fact_key=f"NPI:{prior_npi}",
                    family="AFFILIATION",
                    details={"seed_id": prior_seed.seed_id, "source": prior_seed.source, "reason": prior_seed.reason}
                ))

        # 3. Check Direct TIN Exclusions
        raw_tin = (app.tin or "").strip().upper()
        clean_tin = re.sub(r"\D", "", raw_tin) or raw_tin.replace("-", "")
        tin_seed = self._find_active_seed(self._seeds_by_tin.get(clean_tin), app.application_date)
        if tin_seed:
            statutory_grounds.append("42 CFR 455.416(c) - Tax ID sanctioned under exclusion list")
            verified_facts.append(EvidenceItem(
                code="L1-SEED-TIN-MATCH",
                description=f"Applicant TIN matches sanctioned entity ({tin_seed.source}: {tin_seed.reason}).",
                source_layer="LAYER_1",
                severity=EvidenceSeverity.CRITICAL,
                verified_fact=True,
                fact_key=f"TIN:{clean_tin}",
                family="AFFILIATION",
                details={"seed_id": tin_seed.seed_id, "source": tin_seed.source, "reason": tin_seed.reason}
            ))

        # 4. Check Disclosed Persons (SSN4 + DOB against Seeds & 42 CFR 455.106 criminal convictions)
        for p in app.disclosed_persons:
            key = p.ssn_dob_key.upper()
            person_seed = self._find_active_seed(self._seeds_by_ssn4_dob.get(key), app.application_date)
            if person_seed:
                statutory_grounds.append("42 CFR 455.416(c) - Disclosed owner/officer excluded under 1128(a)(1)")
                verified_facts.append(EvidenceItem(
                    code="L1-SEED-PERSON-EXCLUDED",
                    description=(
                        f"Disclosed individual '{p.full_name}' ({p.role}) matches sanctioned seed record "
                        f"({person_seed.source}: {person_seed.reason})."
                    ),
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.CRITICAL,
                    verified_fact=True,
                    fact_key=f"PERSON:{key}",
                    family="AFFILIATION",
                    details={"person_name": p.full_name, "role": p.role, "source": person_seed.source}
                ))
            if p.has_criminal_record_disclosed:
                verified_facts.append(EvidenceItem(
                    code="L1-DISCLOSED-CRIMINAL-RECORD",
                    description=(
                        f"Disclosed individual '{p.full_name}' ({p.role}) reported prior criminal conviction "
                        "under 42 CFR 455.106. Adjudication and fingerprint verification required."
                    ),
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.HIGH,
                    verified_fact=True,
                    fact_key=f"CRIMINAL:{p.ssn_dob_key or p.full_name}",
                    family="OWNERSHIP",
                    details={"person_name": p.full_name, "role": p.role}
                ))

        # 5. Check EFT Account Shared with Terminated Provider
        if app.eft_account and app.eft_account.routing_number and app.eft_account.account_number:
            eft_k = app.eft_account.account_key.upper()
            eft_seed = self._find_active_seed(self._seeds_by_eft.get(eft_k), app.application_date)
            if eft_seed:
                statutory_grounds.append("42 CFR 455.416(a) - Bank account associated with previously terminated provider")
                verified_facts.append(EvidenceItem(
                    code="L1-SEED-EFT-SHARED-TERMINATION",
                    description=(
                        f"EFT bank account '{app.eft_account.routing_number}-*****' matches bank account of a "
                        f"previously terminated provider ({eft_seed.source}: {eft_seed.reason})."
                    ),
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.CRITICAL,
                    verified_fact=True,
                    fact_key=f"EFT:{eft_k}",
                    family="AFFILIATION",
                    details={"seed_id": eft_seed.seed_id, "reason": eft_seed.reason}
                ))

        # 6. Check Phone Number Matches Sanctioned Entity
        clean_phone = "".join(filter(str.isdigit, app.phone or ""))
        phone_seed = self._find_active_seed(self._seeds_by_phone.get(clean_phone), app.application_date) if clean_phone else None
        if phone_seed:
            statutory_grounds.append("42 CFR 455.416 - Phone number matches sanctioned entity")
            verified_facts.append(EvidenceItem(
                code="L1-SEED-PHONE-MATCH",
                description=(
                    f"Applicant phone number '{app.phone}' matches telephone of a "
                    f"previously sanctioned entity ({phone_seed.source}: {phone_seed.reason})."
                ),
                source_layer="LAYER_1",
                severity=EvidenceSeverity.CRITICAL,
                verified_fact=True,
                fact_key=f"PHONE:{clean_phone}",
                family="AFFILIATION",
                details={"seed_id": phone_seed.seed_id, "source": phone_seed.source, "reason": phone_seed.reason}
            ))

        # 7. Check Address Matches Sanctioned Facility/Location
        if app.primary_address:
            norm_addr = app.primary_address.normalized_single_line.strip().upper()
            addr_seed = self._find_active_seed(self._seeds_by_address.get(norm_addr), app.application_date) if norm_addr else None
            if addr_seed:
                statutory_grounds.append("42 CFR 455.416 - Service address matches sanctioned location")
                verified_facts.append(EvidenceItem(
                    code="L1-SEED-ADDRESS-MATCH",
                    description=(
                        f"Applicant primary address '{app.primary_address.address_line1}' matches address of a "
                        f"previously sanctioned provider ({addr_seed.source}: {addr_seed.reason})."
                    ),
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.CRITICAL,
                    verified_fact=True,
                    fact_key=f"ADDR:{norm_addr}",
                    family="ADDRESS",
                    details={"seed_id": addr_seed.seed_id, "source": addr_seed.source, "reason": addr_seed.reason}
                ))

        # 8. Evaluate Address Rules (DATA-039 Prohibitions) on primary address and secondary locations
        addr_evidence, is_fatal_addr = evaluate_address_rules(
            app.primary_address, app.service_category, config=self.config.address
        )
        verified_facts.extend(addr_evidence)
        if is_fatal_addr:
            statutory_grounds.append("42 CFR 455.416 / DATA-039 - Prohibited or invalid service location")

        for loc in app.practice_locations:
            loc_ev, loc_fatal = evaluate_address_rules(loc, app.service_category, config=self.config.address)
            verified_facts.extend(loc_ev)
            if loc_fatal and "42 CFR 455.416 / DATA-039 - Prohibited or invalid service location" not in statutory_grounds:
                statutory_grounds.append("42 CFR 455.416 / DATA-039 - Prohibited or invalid secondary practice location")

        # 9. Evaluate GR-4 Indirect Ownership Exclusions
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
                        fact_key=f"OWNER:{ind_res.upstream_entity_id}",
                        family="OWNERSHIP",
                        details={
                            "upstream_entity": ind_res.upstream_entity_id,
                            "indirect_pct": ind_res.effective_indirect_pct,
                            "reason": ind_res.seed_reason
                        }
                    ))

        # 9b. Evaluate Disclosed Parent Organizations (Audit §3.7)
        verified_upstreams = {
            ind.upstream_entity_id.strip().upper()
            for ind in (indirect_ownership_results or [])
        }
        for parent_org in (app.parent_organizations or []):
            p_raw = (parent_org or "").strip()
            if not p_raw:
                continue
            p_norm = p_raw.upper()
            p_digits = re.sub(r"\D", "", p_raw)
            matching_seeds = [
                s for s in self.seeds
                if s.identifier_value.strip().upper() == p_norm
                or (p_digits and s.identifier_type in ("NPI", "TIN") and re.sub(r"\D", "", s.identifier_value) == p_digits)
            ]
            direct_parent_seed = self._find_active_seed(matching_seeds, app.application_date)
            if direct_parent_seed:
                statutory_grounds.append(
                    f"42 CFR 455.104 / 455.416 - Disclosed parent organization '{p_raw}' matches sanctioned entity"
                )
                verified_facts.append(EvidenceItem(
                    code="L1-DISCLOSED-PARENT-ORG-EXCLUDED",
                    description=(
                        f"Disclosed parent organization '{p_raw}' matches active exclusion/sanction seed "
                        f"({direct_parent_seed.source}: {direct_parent_seed.reason})."
                    ),
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.CRITICAL,
                    verified_fact=True,
                    fact_key=f"OWNER:{p_norm}",
                    family="OWNERSHIP",
                    details={"parent_organization": p_raw, "seed_id": direct_parent_seed.seed_id}
                ))
            elif p_norm not in verified_upstreams:
                verified_facts.append(EvidenceItem(
                    code="L1-UNVERIFIED-PARENT-ORG",
                    description=(
                        f"Disclosed parent organization '{p_raw}' has no verified indirect ownership chain "
                        "in corporate hierarchy registry. Manual beneficial ownership verification required."
                    ),
                    source_layer="LAYER_1",
                    severity=EvidenceSeverity.MEDIUM,
                    verified_fact=True,
                    fact_key=f"PARENT:{p_norm}",
                    family="OWNERSHIP",
                    details={"parent_organization": p_raw}
                ))

        # 10. Determine Decision Status (Pending human adjudicative confirmation)
        if len(statutory_grounds) > 0:
            status = DecisionStatus.GROUNDS_PENDING_HUMAN_DECISION
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
