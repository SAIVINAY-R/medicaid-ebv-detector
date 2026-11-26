"""Layer 2 Assistive Advisory Scorer & Reviewer Evidence Panel.

Produces explainable queue triage scores, evidence panels, and recommended actions:
- Complies strictly with ADR-0001: Assistive only; never auto-approves or auto-denies
- Complies with Section 5.1 language standard: Uses 'Pattern requires review', never 'Fraud'
- Generates transparent feature contributions adhering to NIST AI RMF standards
- Saturating score transform: priority = 100 * (1 - exp(-S / k))
- Strict feature attribution: sum(contributions.values()) == priority_score
"""

from collections import defaultdict
import math
from typing import Dict, List, Optional, Tuple

from ..config import SystemConfig
from ..models.entities import ProviderApplication
from ..models.results import AdvisoryScore, DecisionStatus, EvidenceItem, EvidenceSeverity, Layer1Decision


class Layer2AdvisoryEngine:
    """Computes assistive priority score and formats explainable evidence panels."""

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or SystemConfig()

    def _infer_family(self, code: str) -> str:
        """Infer evidence family if not explicitly provided."""
        c = code.upper()
        if "OWNER" in c:
            return "OWNERSHIP"
        if "ADDR" in c or "LOCATION" in c or "MAILBOX" in c:
            return "ADDRESS"
        if "TEMPLATE" in c or "VELOCITY" in c or "DEVICE" in c or "IP" in c or "EMAIL" in c or "SUBMISSION" in c:
            return "SUBMISSION"
        if "RESOLVER" in c or "IDENTITY" in c or "MATCH" in c:
            return "IDENTITY"
        if "AGE" in c or "RAPID-FORMATION" in c or "FORMATION" in c:
            return "AGE"
        if "SEED" in c or "GRAPH" in c or "PPR" in c or "AFFILIATION" in c:
            return "AFFILIATION"
        return "SUBMISSION"

    def _get_raw_weight(self, ev: EvidenceItem, app: Optional[ProviderApplication] = None) -> float:
        """Map evidence item to expert-calibrated relative weight."""
        code = ev.code.upper()

        # 1. Affiliation family items
        if "EFT" in code and "SEED" in code:
            return 5.0
        if "PERSON" in code and "SEED" in code:
            return 5.0
        if "NPI" in code and "SEED" in code:
            return 5.0
        if "TIN" in code and "SEED" in code:
            return 5.0
        if "PHONE" in code and "SEED" in code:
            return 3.0
        if code in ("M3-GRAPH-PATH-TO-SEED", "M3-GRAPH-PATH-TO-SANCTIONED-SEED"):
            hops = ev.details.get("hops", ev.details.get("shortest_path_hops", 1))
            return 1.5 if hops >= 3 else 3.0
        if "PPR" in code:
            score = ev.details.get("ppr_score", 0.5)
            return min(2.5, float(score) * 5.0)
        if "MIGRATION" in code:
            return 5.0
        if "CLINICIAN" in code:
            org_count = ev.details.get("unrelated_org_count", 2)
            # Attenuate weight when a clinician legitimately practices across many large hospital/org TINs
            return min(2.5, 1.5 if org_count <= 2 else max(0.5, 1.5 / math.log2(float(org_count))))

        # 2. Ownership family items
        if "INDIRECT-OWNERSHIP" in code:
            return 5.0
        if "CRIMINAL-RECORD" in code:
            return 3.0
        if "PARENT" in code:
            return 2.0

        # 3. Address family items
        if "SEED-ADDRESS" in code:
            return 4.0
        if "HIGH-DENSITY" in code:
            n_providers = ev.details.get("provider_count", 4)
            return min(3.0, max(1.5, math.log2(max(2.0, float(n_providers)))))
        if any(m in code for m in ["CMRA", "POBOX", "PO-BOX", "VIRTUAL", "PMB", "MAIL-DROP", "FACILITY-RESIDENTIAL"]):
            return 3.0
        if "ADDR-MISSING" in code:
            return 3.0
        if "VACANT" in code:
            return 2.0

        # 4. Submission family items
        if "POLICY-CLONE" in code:
            matching_count = ev.details.get("total_matching_applications", ev.details.get("matching_apps_count", 1))
            return min(4.0, 1.5 + math.log2(max(1.0, float(matching_count))))
        if any(v in code for v in ["DEVICE-SHARING", "IP-VELOCITY", "EMAIL-REUSE"]):
            linked = ev.details.get("linked_applications", [])
            count = ev.details.get("recent_count") or (len(linked) if isinstance(linked, list) else 1) or 1
            return min(4.0, 1.5 + math.log2(max(1.0, float(count))))

        # 5. Identity family items
        if "RESOLVER-HIGH-CONFIDENCE" in code:
            prob = ev.details.get("posterior_prob", 0.95)
            return 4.0 * prob
        if "NEAR-MATCH" in code:
            return 1.5

        # 6. Age family items
        if "RAPID-FORMATION" in code:
            if app and app.is_change_of_ownership:
                return 0.0
            days = ev.details.get("days_since_formation", 7)
            if days is not None and days < 30:
                return 1.5
            return 0.75
        if "FORMATION-DATE-MISSING" in code:
            return 0.5

        # Fallback by severity
        sev_map = {
            EvidenceSeverity.CRITICAL: 4.0,
            EvidenceSeverity.HIGH: 2.5,
            EvidenceSeverity.MEDIUM: 1.5,
            EvidenceSeverity.LOW: 0.75,
            EvidenceSeverity.INFO: 0.0
        }
        return sev_map.get(ev.severity, 1.0)

    def compute_advisory_package(
        self,
        app: ProviderApplication,
        layer1_decision: Layer1Decision,
        evidence_items: List[EvidenceItem],
        ppr_risk_score: float = 0.0
    ) -> AdvisoryScore:
        """Synthesize layer signals into an assistive review package.

        Complies strictly with ADR-0001:
        - Layer 1 statutory grounds and ACA tier are NOT added into priority_score
        - Deduplicates facts by fact_key
        - Groups facts into families with diminishing returns and family caps
        - Applies saturating transform priority = 100 * (1 - exp(-S / k))
        - Scales family contributions so sum(contributions.values()) == priority_score
        """
        scoring_cfg = getattr(self.config, "scoring", None)
        k = scoring_cfg.saturation_k if scoring_cfg else 4.0
        family_caps = scoring_cfg.family_caps if scoring_cfg else {
            "AFFILIATION": 6.0,
            "OWNERSHIP": 5.0,
            "ADDRESS": 4.0,
            "SUBMISSION": 4.0,
            "IDENTITY": 4.0,
            "AGE": 1.5
        }
        decay_factors = scoring_cfg.decay_factors if scoring_cfg else [1.0, 0.5, 0.25, 0.125]

        # 1. Deduplicate facts by fact_key, selecting the highest raw weight per fact
        facts_by_key: Dict[str, Tuple[EvidenceItem, float, str]] = {}
        for ev in evidence_items:
            f_key = ev.fact_key or ev.code
            fam = (ev.family or self._infer_family(ev.code)).upper()
            w = self._get_raw_weight(ev, app=app)
            if f_key not in facts_by_key or w > facts_by_key[f_key][1]:
                facts_by_key[f_key] = (ev, w, fam)

        # 2. Check if direct graph path or direct seed match explains affiliation
        has_direct_affiliation = any(
            fam == "AFFILIATION" and "PPR" not in ev.code and ("SEED" in ev.code or "GRAPH-PATH" in ev.code) and w > 0.0
            for ev, w, fam in facts_by_key.values()
        )

        # 3. Suppress PageRank when explicit path explains affiliation; add only when no direct path explains it (Audit §3.2, §3.6)
        if has_direct_affiliation:
            facts_by_key.pop("PPR:SEED_PROXIMITY", None)
        elif "PPR:SEED_PROXIMITY" not in facts_by_key and ppr_risk_score > 0.0:
            ppr_weight = min(2.5, ppr_risk_score * 5.0)
            ppr_ev = EvidenceItem(
                code="M3-PPR-GRAPH-PROXIMITY",
                description=f"Personalized PageRank seed proximity score: {ppr_risk_score:.3f}",
                source_layer="M3_GRAPH",
                severity=EvidenceSeverity.HIGH if ppr_risk_score >= 0.4 else EvidenceSeverity.MEDIUM,
                verified_fact=False,
                fact_key="PPR:SEED_PROXIMITY",
                family="AFFILIATION",
                details={"ppr_score": ppr_risk_score}
            )
            facts_by_key["PPR:SEED_PROXIMITY"] = (ppr_ev, ppr_weight, "AFFILIATION")

        # 4. Group unique non-zero-weight facts by family and apply diminishing returns
        family_weights: Dict[str, List[float]] = defaultdict(list)
        for ev, w, fam in facts_by_key.values():
            if w > 0.0:
                family_weights[fam].append(w)

        capped_family_scores: Dict[str, float] = {}
        for fam, weights in family_weights.items():
            weights.sort(reverse=True)
            fam_score = 0.0
            for i, w in enumerate(weights):
                factor = decay_factors[i] if i < len(decay_factors) else decay_factors[-1] * (0.5 ** (i - len(decay_factors) + 1))
                fam_score += w * factor
            cap = family_caps.get(fam, 5.0)
            capped = min(cap, fam_score)
            if capped > 0.0:
                capped_family_scores[fam] = capped

        # 5. Saturating Score Transform: S = sum(capped_family_scores), priority = 100 * (1 - exp(-S / k))
        raw_s = sum(capped_family_scores.values())
        if raw_s > 0.0:
            priority_score = 100.0 * (1.0 - math.exp(-raw_s / k))
            priority_score_rounded = round(min(100.0, max(0.0, priority_score)), 1)
        else:
            priority_score = 0.0
            priority_score_rounded = 0.0

        # 6. Compute Normalized Contributions summing exactly to priority_score_rounded
        contributions: Dict[str, float] = {}
        if raw_s > 0.0 and priority_score_rounded > 0.0:
            for fam, score in capped_family_scores.items():
                fam_key = fam.lower()
                contributions[fam_key] = round(priority_score_rounded * (score / raw_s), 1)

            # Ensure sum(contributions) == priority_score_rounded by reconciling rounding residual
            diff = round(priority_score_rounded - sum(contributions.values()), 1)
            if abs(diff) > 0.001 and contributions:
                max_fam = max(contributions.keys(), key=lambda f: contributions[f])
                contributions[max_fam] = round(contributions[max_fam] + diff, 1)

        # 7. Derive Composite Risk Level from score capacity bands (§3.16)
        if priority_score_rounded >= 70.0:
            risk_level = "CRITICAL"
        elif priority_score_rounded >= 40.0:
            risk_level = "HIGH"
        elif priority_score_rounded >= 20.0:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # 8. Recommend Actions following the specific evidence families present (§3.1)
        actions = []
        if capped_family_scores.get("ADDRESS", 0.0) > 0.0:
            actions.append("UNANNOUNCED_SITE_VISIT (Verify physical operational facility under 42 CFR 455.432)")
        if capped_family_scores.get("AFFILIATION", 0.0) > 0.0:
            has_eft = any("EFT" in ev.code for ev, _, _ in facts_by_key.values())
            has_person = any("PERSON" in ev.code for ev, _, _ in facts_by_key.values())
            if has_eft:
                actions.append("REQUEST_BANK_ACCOUNT_PROOF (Verify EFT ownership under business TIN)")
            if has_person:
                actions.append("REQUIRE_FINGERPRINT_CRIMINAL_CHECKS (For all 5%+ and managing owners under 42 CFR 455.434)")
            if any("SEED" in ev.code for ev, _, _ in facts_by_key.values()):
                actions.append("REFER_TO_PROGRAM_INTEGRITY (Credible Allegation of Fraud evaluation under 42 CFR 455.23)")
        if capped_family_scores.get("OWNERSHIP", 0.0) > 0.0:
            actions.append("REQUEST_DISCLOSURE_CLARIFICATION (Verify complete 5%+ ownership chain under 42 CFR 455.104)")
        if capped_family_scores.get("IDENTITY", 0.0) > 0.0:
            actions.append("RESOLVE_NEAR_MATCH_QUEUE (Review candidate identity linkage)")
        if capped_family_scores.get("SUBMISSION", 0.0) > 0.0:
            actions.append("DESK_REVIEW_SUBMISSION_INTEGRITY (Review template similarity and submission telemetry)")

        if not actions:
            actions.append("STANDARD_STREAMLINED_PROCESSING")

        # 9. Non-inflammatory explanation summary (§5.1 compliant)
        sorted_fams = sorted(capped_family_scores.items(), key=lambda x: x[1], reverse=True)
        active_fams = [f"{fam.title()} ({score:.1f} pts)" for fam, score in sorted_fams if score > 0]
        fams_str = ", ".join(active_fams) if active_fams else "none"

        if priority_score_rounded >= 70.0:
            summary = (
                f"Pattern requires priority human review (Priority Score: {priority_score_rounded:.1f}/100, Level: {risk_level}). "
                f"Primary risk drivers: {fams_str}."
            )
        elif priority_score_rounded >= 40.0:
            summary = (
                f"Pattern requires expedited review (Priority Score: {priority_score_rounded:.1f}/100, Level: {risk_level}). "
                f"Primary risk drivers: {fams_str}."
            )
        elif priority_score_rounded >= 20.0:
            summary = (
                f"Pattern requires standard review (Priority Score: {priority_score_rounded:.1f}/100, Level: {risk_level}). "
                f"Notable factors: {fams_str}."
            )
        else:
            summary = (
                f"Application exhibits standard baseline indicators (Priority Score: {priority_score_rounded:.1f}/100, Level: {risk_level}). "
                "No elevated risk patterns detected."
            )

        # Pass unique deduplicated evidence items list
        deduped_evidence = [item[0] for item in facts_by_key.values()]

        return AdvisoryScore(
            application_id=app.application_id,
            priority_score=priority_score_rounded,
            composite_risk_level=risk_level,
            evidence_items=deduped_evidence,
            recommended_review_actions=actions,
            explanation_summary=summary,
            feature_contributions=contributions
        )
