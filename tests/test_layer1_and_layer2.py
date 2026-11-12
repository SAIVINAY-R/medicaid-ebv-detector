"""Unit tests for Layer 1 Authoritative Rules, Layer 2 Advisory Scorer, and WORM Audit."""

import unittest

from ebv_detector.models.entities import AddressRecord, EFTAccount, PersonDisclosure, ProviderApplication, SeedRecord
from ebv_detector.models.results import DecisionStatus, EvidenceItem, EvidenceSeverity
from ebv_detector.pipeline.audit import WormAuditLogger
from ebv_detector.pipeline.layer1_rules import Layer1RulesEngine
from ebv_detector.pipeline.layer2_advisory import Layer2AdvisoryEngine


class TestLayer1AndLayer2(unittest.TestCase):

    def setUp(self):
        self.seed = SeedRecord(
            seed_id="SEED-LEIE-01",
            identifier_type="TIN",
            identifier_value="99-8877665",
            source="OIG_LEIE",
            reason="Exclusion under 1128(a)(1)",
            effective_date="2024-01-01"
        )
        self.l1_engine = Layer1RulesEngine(seeds=[self.seed])
        self.l2_engine = Layer2AdvisoryEngine()
        self.audit_logger = WormAuditLogger()

    def test_layer1_tin_exclusion_triggers_denial_grounds(self):
        app = ProviderApplication(
            application_id="APP-EXCL-TIN",
            npi="1900000055",
            legal_business_name="Sanctioned Shell LLC",
            tin="99-8877665", # Matches seed
            service_category="COMMUNITY_MENTAL_HEALTH",
            primary_address=AddressRecord(
                address_line1="100 Medical Way",
                city="Raleigh",
                state="NC",
                zip5="27601",
                rdi="COMMERCIAL"
            )
        )
        dec = self.l1_engine.evaluate_application(app)
        self.assertEqual(dec.status, DecisionStatus.DENIED_GROUNDS)
        self.assertFalse(dec.can_auto_approve)
        self.assertIn("42 CFR 455.416(c) - Tax ID sanctioned under exclusion list", dec.statutory_grounds)

    def test_layer1_aca_risk_tier_determination(self):
        # HCBS is categorically High Risk
        app_hcbs = ProviderApplication(
            application_id="APP-HCBS",
            npi="1900000066",
            legal_business_name="Carolina Home Care",
            tin="12-9988776",
            service_category="HCBS",
            primary_address=AddressRecord(address_line1="100 Main St", city="Raleigh", state="NC", zip5="27601")
        )
        tier, reasons = self.l1_engine.assign_aca_risk_tier(app_hcbs)
        self.assertEqual(tier, "HIGH")

        # Family practice is categorically Limited Risk
        app_fp = ProviderApplication(
            application_id="APP-FP",
            npi="1900000077",
            legal_business_name="Wake Family Medicine",
            tin="12-5544332",
            service_category="FAMILY_PRACTICE",
            primary_address=AddressRecord(address_line1="100 Main St", city="Raleigh", state="NC", zip5="27601")
        )
        tier2, reasons2 = self.l1_engine.assign_aca_risk_tier(app_fp)
        self.assertEqual(tier2, "LIMITED")

    def test_layer2_advisory_scoring_and_explanation(self):
        app = ProviderApplication(
            application_id="APP-ADVISORY-TEST",
            npi="1900000088",
            legal_business_name="Advisory Test Clinic",
            tin="56-1122334"
        )
        l1_dec = self.l1_engine.evaluate_application(app)
        ev_items = [
            EvidenceItem(
                code="M4-ANOMALY-RAPID-FORMATION",
                description="Formed 7 days prior to application",
                source_layer="M4_ANOMALY",
                severity=EvidenceSeverity.HIGH,
                verified_fact=True
            )
        ]

        advisory = self.l2_engine.compute_advisory_package(app, l1_dec, ev_items, ppr_risk_score=0.45)
        self.assertIn("Pattern requires", advisory.explanation_summary)
        self.assertGreater(advisory.priority_score, 20.0)
        self.assertTrue(len(advisory.recommended_review_actions) > 0)

    def test_worm_audit_chaining_and_sha256(self):
        app = ProviderApplication(
            application_id="APP-AUDIT-01",
            npi="1900000099",
            legal_business_name="Audit Logging Clinic",
            tin="56-9900112"
        )
        l1_dec = self.l1_engine.evaluate_application(app)
        advisory = self.l2_engine.compute_advisory_package(app, l1_dec, [])
        
        # Log event
        rec1 = self.audit_logger.log_decision_event(app, l1_dec, advisory)
        self.assertTrue(len(rec1.feature_vector_sha256) == 64)
        self.assertTrue(len(rec1.audit_hash) == 64)

        # Log reviewer override
        updated = self.audit_logger.record_reviewer_override(
            record_id=rec1.record_id,
            reviewer_id="INVESTIGATOR-42",
            action="REFERRED_TO_PI",
            notes="Confirmed undisclosed holding company linkage."
        )
        self.assertEqual(updated.reviewer_action, "REFERRED_TO_PI")
        self.assertEqual(updated.reviewer_notes, "Confirmed undisclosed holding company linkage.")


if __name__ == "__main__":
    unittest.main()

