"""Real-world edge cases and stress tests for EBV fraud detection pipeline."""

import unittest
from datetime import datetime

from ebv_detector.claims.billing_rules import evaluate_hard_billing_edits
from ebv_detector.claims.patient_sharing import PatientMigrationDetector
from ebv_detector.claims.ramp_detector import BillingRampDetector
from ebv_detector.config import SystemConfig
from ebv_detector.entity_resolution.fellegi_sunter import FellegiSunterResolver
from ebv_detector.models.entities import AddressRecord, ClaimRecord, EFTAccount, PersonDisclosure, ProviderApplication, SeedRecord
from ebv_detector.models.results import DecisionStatus, EvidenceItem, EvidenceSeverity
from ebv_detector.pipeline.audit import WormAuditLogger
from ebv_detector.pipeline.checkpoint1 import Checkpoint1Pipeline
from ebv_detector.pipeline.checkpoint2 import Checkpoint2Pipeline
from ebv_detector.pipeline.layer1_rules import Layer1RulesEngine
from ebv_detector.pipeline.layer2_advisory import Layer2AdvisoryEngine


class TestRealWorldEdgeCases(unittest.TestCase):

    def setUp(self):
        self.config = SystemConfig()
        self.active_seed = SeedRecord(
            seed_id="SEED-ACTIVE",
            identifier_type="TIN",
            identifier_value="99-1122334",
            source="OIG_LEIE",
            reason="Exclusion under 1128(a)(1)",
            effective_date="2020-01-01",
            end_date="2030-01-01"
        )
        self.expired_seed = SeedRecord(
            seed_id="SEED-EXPIRED",
            identifier_type="TIN",
            identifier_value="88-5544332",
            source="OIG_LEIE",
            reason="Prior exclusion reinstated",
            effective_date="2010-01-01",
            end_date="2022-01-01"  # Expired before 2026 application date
        )
        self.future_seed = SeedRecord(
            seed_id="SEED-FUTURE",
            identifier_type="NPI",
            identifier_value="1900009999",
            source="STATE_CAF",
            reason="Future effective date sanction",
            effective_date="2027-01-01"  # Future effective date
        )
        self.l1_engine = Layer1RulesEngine(
            seeds=[self.active_seed, self.expired_seed, self.future_seed],
            config=self.config
        )
        self.l2_engine = Layer2AdvisoryEngine(config=self.config)

    def test_minimal_application_with_none_and_empty_fields(self):
        """Pipeline must gracefully evaluate applications with missing/None optional fields."""
        # 1. Missing primary address triggers statutory service location requirement (42 CFR 455.416 / DATA-039)
        app_missing_addr = ProviderApplication(
            application_id="APP-NO-ADDR",
            npi="1900000002",
            legal_business_name="Minimal Clinical Services",
            tin="56-0000002",
            phone="",
            email="",
            primary_address=None,
            eft_account=None,
            disclosed_persons=[],
            device_fingerprint_id=None,
            submission_ip_address=None,
            policy_manual_text=None
        )
        dec_no_addr = self.l1_engine.evaluate_application(app_missing_addr)
        self.assertEqual(dec_no_addr.status, DecisionStatus.GROUNDS_PENDING_HUMAN_DECISION)
        self.assertIn("42 CFR 455.416 / DATA-039 - Prohibited or invalid service location", dec_no_addr.statutory_grounds)

        # 2. Valid address but empty optional fields (no phone, email, EFT, disclosed persons) auto-approves cleanly
        app_minimal = ProviderApplication(
            application_id="APP-MINIMAL-01",
            npi="1900000002",
            legal_business_name="Minimal Clinical Services",
            tin="56-0000002",
            service_category="FAMILY_PRACTICE",
            phone="",
            email="",
            primary_address=AddressRecord(
                address_line1="100 Commercial Blvd",
                city="Raleigh",
                state="NC",
                zip5="27601",
                is_cmra=False,
                rdi="COMMERCIAL"
            ),
            eft_account=None,
            disclosed_persons=[],
            device_fingerprint_id=None,
            submission_ip_address=None,
            policy_manual_text=None
        )
        dec = self.l1_engine.evaluate_application(app_minimal)
        self.assertEqual(dec.status, DecisionStatus.AUTO_APPROVE_ELIGIBLE)
        self.assertTrue(dec.can_auto_approve)

        # Layer 2 advisory package produces zero score and clean low risk
        advisory = self.l2_engine.compute_advisory_package(app_minimal, dec, dec.verified_facts)
        self.assertEqual(advisory.priority_score, 0.0)
        self.assertEqual(advisory.composite_risk_level, "LOW")
        self.assertEqual(len(advisory.feature_contributions), 0)

    def test_unparseable_formation_date_handling(self):
        """Unparseable formation date returns None instead of substituting 999 days."""
        app = ProviderApplication(
            application_id="APP-DATE-ERR",
            npi="1900000003",
            legal_business_name="Date Error Health Corp",
            tin="56-0000003",
            formation_date="INVALID_DATE_FORMAT",
            application_date="2026-02-01"
        )
        self.assertIsNone(app.days_since_formation)

    def test_seed_expiration_and_future_effective_date(self):
        """Reinstated/expired seeds and future-dated sanctions do not trigger grounds."""
        clean_addr = AddressRecord(
            address_line1="100 Medical Way",
            city="Raleigh",
            state="NC",
            zip5="27601",
            is_cmra=False,
            rdi="COMMERCIAL"
        )

        # App matching expired TIN
        app_expired = ProviderApplication(
            application_id="APP-REINSTATED",
            npi="1900000004",
            legal_business_name="Reinstated Clinic LLC",
            tin="88-5544332",  # Matches expired seed
            service_category="FAMILY_PRACTICE",
            primary_address=clean_addr,
            application_date="2026-02-01"
        )
        dec_expired = self.l1_engine.evaluate_application(app_expired)
        self.assertEqual(dec_expired.status, DecisionStatus.AUTO_APPROVE_ELIGIBLE)
        self.assertEqual(len(dec_expired.statutory_grounds), 0)

        # App matching future NPI sanction
        app_future = ProviderApplication(
            application_id="APP-FUTURE-SEED",
            npi="1900009999",  # Matches future seed
            legal_business_name="Future Sanction Clinic LLC",
            tin="56-0000005",
            service_category="FAMILY_PRACTICE",
            primary_address=clean_addr,
            application_date="2026-02-01"
        )
        dec_future = self.l1_engine.evaluate_application(app_future)
        self.assertEqual(dec_future.status, DecisionStatus.AUTO_APPROVE_ELIGIBLE)
        self.assertEqual(len(dec_future.statutory_grounds), 0)

        # App matching active TIN
        app_active = ProviderApplication(
            application_id="APP-ACTIVE-SEED",
            npi="1900000006",
            legal_business_name="Active Sanctioned Shell LLC",
            tin="99-1122334",  # Matches active seed
            service_category="FAMILY_PRACTICE",
            primary_address=clean_addr,
            application_date="2026-02-01"
        )
        dec_active = self.l1_engine.evaluate_application(app_active)
        self.assertEqual(dec_active.status, DecisionStatus.GROUNDS_PENDING_HUMAN_DECISION)
        self.assertGreater(len(dec_active.statutory_grounds), 0)

    def test_score_saturation_and_exact_feature_contributions_equality(self):
        """Even with an extreme barrage of anomalies, priority score never exceeds 100.0,
        and sum of feature contributions equals priority score exactly."""
        app = ProviderApplication(
            application_id="APP-EXTREME-ANOMALY",
            npi="1900000007",
            legal_business_name="Extreme Barrage Fraud Ring Inc",
            tin="56-9999999"
        )
        l1_dec = self.l1_engine.evaluate_application(app)

        # Barrage of 15 high-severity evidence items across all 6 families
        barrage_evidence = [
            EvidenceItem(code="L1-SEED-EFT-SHARED-TERMINATION", description="Shared EFT", source_layer="LAYER_1", severity=EvidenceSeverity.CRITICAL, verified_fact=True, fact_key="EFT:053_111", family="AFFILIATION"),
            EvidenceItem(code="M3-GRAPH-PATH-TO-SANCTIONED-SEED", description="Graph path", source_layer="M3_GRAPH", severity=EvidenceSeverity.CRITICAL, verified_fact=True, fact_key="PATH:SEED_01", family="AFFILIATION"),
            EvidenceItem(code="L1-SEED-PHONE-MATCH", description="Phone match", source_layer="LAYER_1", severity=EvidenceSeverity.CRITICAL, verified_fact=True, fact_key="PHONE:9195551212", family="AFFILIATION"),
            EvidenceItem(code="L1-GR4-INDIRECT-OWNERSHIP-EXCLUDED", description="GR4 owner", source_layer="LAYER_1", severity=EvidenceSeverity.CRITICAL, verified_fact=True, fact_key="OWNER:UPSTREAM_HOLDING", family="OWNERSHIP"),
            EvidenceItem(code="M2-ADDR-COMMERCIAL-MAIL-DROP", description="CMRA mailbox", source_layer="M2_ADDR", severity=EvidenceSeverity.CRITICAL, verified_fact=True, fact_key="ADDR:100_MAIN", family="ADDRESS"),
            EvidenceItem(code="M2-ADDR-HIGH-DENSITY-CLUSTER", description="High density", source_layer="M2_ADDR", severity=EvidenceSeverity.MEDIUM, verified_fact=False, fact_key="ADDR:100_MAIN", family="ADDRESS"),
            EvidenceItem(code="M4-TEMPLATE-POLICY-CLONE", description="Policy clone", source_layer="M4_ANOMALY", severity=EvidenceSeverity.HIGH, verified_fact=False, fact_key="TEMPLATE:CLONE", family="SUBMISSION", details={"matching_apps_count": 8}),
            EvidenceItem(code="M4-ANOMALY-DEVICE-SHARING", description="Device velocity", source_layer="M4_ANOMALY", severity=EvidenceSeverity.HIGH, verified_fact=False, fact_key="DEV:FINGERPRINT_1", family="SUBMISSION", details={"recent_count": 5}),
            EvidenceItem(code="M1-RESOLVER-HIGH-CONFIDENCE-LINK", description="Adverse link", source_layer="M1_RESOLVER", severity=EvidenceSeverity.HIGH, verified_fact=False, fact_key="LINK:ADVERSE_ENTITY", family="IDENTITY", details={"posterior_prob": 0.99}),
            EvidenceItem(code="M4-ANOMALY-RAPID-FORMATION", description="Rapid formation", source_layer="M4_ANOMALY", severity=EvidenceSeverity.MEDIUM, verified_fact=False, fact_key="AGE:RAPID", family="AGE", details={"days_since_formation": 3}),
        ]

        advisory = self.l2_engine.compute_advisory_package(app, l1_dec, barrage_evidence, ppr_risk_score=0.95)
        # Verify strict bounds
        self.assertLessEqual(advisory.priority_score, 100.0)
        self.assertGreater(advisory.priority_score, 90.0)
        self.assertEqual(advisory.composite_risk_level, "CRITICAL")

        # Verify strict mathematical equality: sum(contributions) == priority_score
        contributions_sum = round(sum(advisory.feature_contributions.values()), 1)
        self.assertEqual(contributions_sum, advisory.priority_score)

        # Verify actions follow evidence types
        self.assertTrue(any("SITE_VISIT" in a for a in advisory.recommended_review_actions))
        self.assertTrue(any("BANK_ACCOUNT" in a for a in advisory.recommended_review_actions))
        self.assertTrue(any("PROGRAM_INTEGRITY" in a for a in advisory.recommended_review_actions))

    def test_worm_audit_chain_with_multiple_sequential_overrides(self):
        """WORM audit logger preserves cryptographic integrity across multiple chained overrides."""
        logger = WormAuditLogger()
        app = ProviderApplication(
            application_id="APP-MULTI-OVERRIDE",
            npi="1900000008",
            legal_business_name="Audit Chain Test LLC",
            tin="56-0000008"
        )
        l1_dec = self.l1_engine.evaluate_application(app)
        adv = self.l2_engine.compute_advisory_package(app, l1_dec, [])

        # 1. Automated evaluation event
        rec1 = logger.log_decision_event(app, l1_dec, adv)
        self.assertEqual(rec1.event_type, "SYSTEM_INFERENCE")
        self.assertIsNone(rec1.parent_record_id)
        self.assertTrue(logger.verify_chain())

        # 2. First reviewer override (Desk Reviewer)
        rec2 = logger.record_reviewer_override(
            record_id=rec1.record_id,
            reviewer_id="DESK_REV_01",
            action="REQUEST_INFO",
            notes="Sent 10-day letter requesting utility bill verification."
        )
        self.assertEqual(rec2.event_type, "REVIEWER_DECISION")
        self.assertEqual(rec2.parent_record_id, rec1.record_id)
        self.assertTrue(logger.verify_chain())

        # 3. Second reviewer override (Program Integrity Investigator)
        rec3 = logger.record_reviewer_override(
            record_id=rec2.record_id,
            reviewer_id="PI_INVESTIGATOR_07",
            action="REFERRED_TO_DOJ",
            notes="Confirmed organized billing fraud ring connection."
        )
        self.assertEqual(rec3.event_type, "REVIEWER_DECISION")
        self.assertEqual(rec3.parent_record_id, rec2.record_id)
        self.assertTrue(logger.verify_chain())

        # All 3 records exist in immutable history
        records = logger.get_all_records()
        self.assertEqual(len(records), 3)

    def test_claims_surveillance_with_empty_and_zero_values(self):
        """Checkpoint 2 billing surveillance handles zero-claim providers gracefully."""
        cp2 = Checkpoint2Pipeline(config=self.config)
        app = ProviderApplication(
            application_id="APP-ZERO-CLAIMS",
            npi="1900000009",
            legal_business_name="Zero Billing Clinic LLC",
            tin="56-0000009"
        )
        res = cp2.evaluate_provider_early_billing(app, months_enrolled=1, claims=[])
        self.assertIsNotNone(res)
        self.assertFalse(res.trigger_payment_suspension)
        self.assertFalse(res.trigger_unannounced_site_visit)
        self.assertEqual(len(res.total_evidence_items), 0)

    def test_checkpoint1_pipeline_read_only_scoring_idempotency(self):
        """Evaluating the same application multiple times produces identical, idempotent scores."""
        cp1 = Checkpoint1Pipeline(self.config, [self.active_seed])
        app = ProviderApplication(
            application_id="APP-IDEMPOTENT-01",
            npi="1900000010",
            legal_business_name="Idempotent Test Clinic LLC",
            tin="56-0000010",
            phone="919-555-7711",
            primary_address=AddressRecord(
                address_line1="500 Medical Drive",
                city="Cary",
                state="NC",
                zip5="27511",
                is_cmra=False,
                rdi="COMMERCIAL"
            )
        )
        pkg1 = cp1.evaluate_application(app)
        pkg2 = cp1.evaluate_application(app)

        self.assertEqual(pkg1.layer1_decision.status, pkg2.layer1_decision.status)
        self.assertEqual(pkg1.advisory_score.priority_score, pkg2.advisory_score.priority_score)
        self.assertEqual(pkg1.advisory_score.composite_risk_level, pkg2.advisory_score.composite_risk_level)


if __name__ == "__main__":
    unittest.main()
