"""Layer 2 Assistive Advisory Scorer & Reviewer Evidence Panel.

Produces explainable queue triage scores, evidence panels, and recommended actions:
- Complies strictly with ADR-0001: Assistive only; never auto-approves or auto-denies
- Complies with Section 5.1 language standard: Uses 'Pattern requires review', never 'Fraud'
- Generates transparent feature contributions adhering to NIST AI RMF standards
"""

from typing import Dict, List, Optional

from ..models.entities import ProviderApplication
from ..models.results import AdvisoryScore, DecisionStatus, EvidenceItem, EvidenceSeverity, Layer1Decision


class Layer2AdvisoryEngine:
    """Computes assistive priority score and formats explainable evidence panels."""

    def compute_advisory_package(
        self,
        app: ProviderApplication,
        layer1_decision: Layer1Decision,
        evidence_items: List[EvidenceItem],
        ppr_risk_score: float = 0.0
    ) -> AdvisoryScore:
        """Synthesize all layer signals into an assistive review package."""
        # 1. Base Score from Layer 1 Decision Status
        score = 0.0
        contributions: Dict[str, float] = {}

        if layer1_decision.status.value in ("DENIED_GROUNDS", "REFERRED_TO_PI"):
            score += 50.0
            contributions["layer1_statutory_grounds"] = 50.0
        elif layer1_decision.assigned_aca_risk_tier == "HIGH":
            score += 25.0
            contributions["aca_tier_high"] = 25.0
        elif layer1_decision.assigned_aca_risk_tier == "MODERATE":
            score += 10.0
            contributions["aca_tier_moderate"] = 10.0

        # 2. Add Contributions from Evidence Items
        sev_weights = {
            EvidenceSeverity.CRITICAL: 20.0,
            EvidenceSeverity.HIGH: 12.0,
            EvidenceSeverity.MEDIUM: 6.0,
            EvidenceSeverity.LOW: 2.0,
            EvidenceSeverity.INFO: 0.0
        }

        near_match_points = 0.0
        for ev in evidence_items:
            # Cap cumulative near-match queue score so clean apps don't get inflated
            if ev.code == "M1-RESOLVER-NEAR-MATCH-ENQUEUED":
                if near_match_points >= 10.0:
                    continue
                near_match_points += 5.0
                weight = 5.0
            else:
                weight = sev_weights.get(ev.severity, 2.0)
            score += weight
            contributions[ev.code] = contributions.get(ev.code, 0.0) + weight

        # 3. Add Contribution from Seed Personalized PageRank
        if ppr_risk_score > 0.0:
            ppr_points = min(25.0, ppr_risk_score * 25.0)
            score += ppr_points
            contributions["m3_seed_proximity_ppr"] = round(ppr_points, 1)

        # Cap score between 0.0 and 100.0
        final_priority = min(100.0, max(0.0, score))

        # Risk level categorization
        has_critical_ev = any(e.severity == EvidenceSeverity.CRITICAL for e in evidence_items)
        has_high_ev = any(e.severity == EvidenceSeverity.HIGH for e in evidence_items)

        if final_priority >= 75.0 or len(layer1_decision.statutory_grounds) > 0 or has_critical_ev:
            risk_level = "CRITICAL"
        elif final_priority >= 45.0 or has_high_ev:
            risk_level = "HIGH"
        elif final_priority >= 20.0 or layer1_decision.status == DecisionStatus.REVIEW_REQUIRED:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # 4. Formulate Recommended Actions
        actions = []
        if risk_level == "CRITICAL" or len(layer1_decision.statutory_grounds) > 0:
            actions.extend([
                "REFER_TO_PROGRAM_INTEGRITY (Credible Allegation of Fraud evaluation)",
                "MANDATORY_UNANNOUNCED_SITE_VISIT (Verify physical operational clinic)",
                "HOLD_CLAIMS_PREPAYMENT_REVIEW (Prevent improper disbursements)"
            ])
        elif risk_level == "HIGH":
            actions.extend([
                "ORDER_UNANNOUNCED_SITE_VISIT (Verify practice location existence)",
                "REQUEST_BANK_ACCOUNT_PROOF (Verify EFT ownership under business TIN)",
                "REQUIRE_FINGERPRINT_CRIMINAL_CHECKS (For all 5%+ and managing owners)"
            ])
        elif risk_level == "MEDIUM":
            actions.append("REQUEST_ADDITIONAL_INFORMATION (Resolve near-match / address inquiries)")
        else:
            actions.append("STANDARD_STREAMLINED_PROCESSING")

        # 5. Non-inflammatory explanation summary (§5.1 compliant)
        critical_codes = [e.code for e in evidence_items if e.severity == EvidenceSeverity.CRITICAL]
        high_codes = [e.code for e in evidence_items if e.severity == EvidenceSeverity.HIGH]
        
        if critical_codes:
            summary = (
                f"Pattern requires priority human review due to {len(critical_codes)} critical screening indicators "
                f"({', '.join(critical_codes[:3])}). Direct regulatory grounds or seed linkages present."
            )
        elif high_codes:
            summary = (
                f"Pattern requires expedited review based on {len(high_codes)} elevated anomaly signals "
                f"({', '.join(high_codes[:3])}). Site visit and identity corroboration recommended."
            )
        else:
            summary = "Application exhibits standard baseline indicators. No critical anomaly patterns detected."

        return AdvisoryScore(
            application_id=app.application_id,
            priority_score=round(final_priority, 1),
            composite_risk_level=risk_level,
            evidence_items=evidence_items,
            recommended_review_actions=actions,
            explanation_summary=summary,
            feature_contributions=contributions
        )
