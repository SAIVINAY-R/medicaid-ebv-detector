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

    def test_graph_substring_cycle_and_temporal_edge_filtering(self):
        """Graph traversal distinguishes prefix-overlapping node IDs and filters expired edges."""
        from ebv_detector.graph.schema import EdgeType, GraphEdge, GraphNode, NodeType
        from ebv_detector.graph.store import GraphStore
        from ebv_detector.graph.traversal import GraphTraversalEngine

        store = GraphStore()
        traversal = GraphTraversalEngine(store, max_hops=4)
        try:
            # ORG-10 is substring-ambiguous if checked against ORG-1 without delimiters
            store.add_node(GraphNode("ORG-10", NodeType.PROVIDER, "Clinic 10"))
            store.add_node(GraphNode("ORG-1", NodeType.ORGANIZATION, "Parent 1"))
            store.add_node(GraphNode("SEED-1", NodeType.SEED, "Excluded Seed", is_seed=True, seed_source="LEIE"))

            store.add_edge(GraphEdge("ORG-1", "ORG-10", EdgeType.DIRECT_OWNERSHIP, ownership_percentage=0.5, effective_start="2020-01-01"))
            store.add_edge(GraphEdge("SEED-1", "ORG-1", EdgeType.DIRECT_OWNERSHIP, ownership_percentage=0.5, effective_start="2020-01-01"))

            # Should discover ORG-10 -> ORG-1 -> SEED-1 cleanly (0.25 indirect equity)
            paths = traversal.find_paths_to_seeds("ORG-10", as_of_date="2026-02-01")
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].hops, 2)

            ind = traversal.calculate_gr4_indirect_ownership("ORG-10", as_of_date="2026-02-01")
            seed_ind = next(r for r in ind if r.upstream_entity_id == "SEED-1")
            self.assertAlmostEqual(seed_ind.effective_indirect_pct, 0.25, places=3)

            # Now test expired ownership edge: sold stake in 2022
            store.add_node(GraphNode("ORG-FORMER", NodeType.PROVIDER, "Divested Clinic"))
            store.add_edge(GraphEdge(
                "SEED-1", "ORG-FORMER", EdgeType.DIRECT_OWNERSHIP,
                ownership_percentage=0.5, effective_start="2019-01-01", effective_end="2022-12-31"
            ))
            ind_past = traversal.calculate_gr4_indirect_ownership("ORG-FORMER", as_of_date="2026-02-01")
            self.assertEqual(len(ind_past), 0)
            ind_during = traversal.calculate_gr4_indirect_ownership("ORG-FORMER", as_of_date="2021-06-01")
            self.assertEqual(len(ind_during), 1)
        finally:
            store.close()

    def test_same_tin_organization_exempt_from_velocity_and_template_false_positives(self):
        """A legitimate multi-site clinic sharing one TIN does not falsely trigger burst/template flags."""
        from ebv_detector.anomaly.application import ApplicationVelocityTracker
        from ebv_detector.anomaly.template import MinHashTemplateDetector

        tracker = ApplicationVelocityTracker(self.config.anomaly)
        tpl = MinHashTemplateDetector(self.config.anomaly)
        shared_manual = (
            "CLINICAL QUALITY GOVERNANCE AND COMPLIANCE STANDARDS: CARE GROUP OPERATIONS. "
            "SECTION 1: STAFF CREDENTIALING AND BOARD SUPERVISION ACROSS ALL LOCATIONS. "
            "SECTION 2: QUALITY ASSURANCE AND HIPAA PRIVACY MONITORING."
        )

        # Same TIN submitting 4 sites via central compliance officer & corporate manual
        for i in range(4):
            app = ProviderApplication(
                application_id=f"APP-MULTISITE-{i}",
                npi=f"190001000{i}",
                legal_business_name=f"Regional Health Clinic Site {i}",
                tin="56-8888888",  # Same corporate TIN
                formation_date="2015-01-01",
                application_date="2026-02-01",
                device_fingerprint_id="DEV-CORP-CREDENTIALER",
                submission_ip_address="192.0.2.50",
                email="credentialing@regionalhealth.org",
                policy_manual_text=shared_manual
            )
            vel_ev = tracker.record_and_evaluate(app)
            tpl_ev = tpl.register_and_evaluate(app)
            self.assertEqual(vel_ev, [])
            self.assertEqual(tpl_ev, [])

    def test_chow_exempts_rapid_formation_and_criminal_disclosure_flagged(self):
        """Change-of-ownership (CHOW) exempts age alert; disclosed criminal record flags REVIEW_REQUIRED."""
        from ebv_detector.anomaly.application import ApplicationVelocityTracker

        tracker = ApplicationVelocityTracker(self.config.anomaly)
        addr = AddressRecord(address_line1="200 Clinic Way", city="Raleigh", state="NC", zip5="27601", rdi="COMMERCIAL")
        app = ProviderApplication(
            application_id="APP-CHOW-CRIM",
            npi="1900020001",
            legal_business_name="New Buyer Operations LLC",
            tin="56-2222333",
            service_category="FAMILY_PRACTICE",
            formation_date="2026-01-29",  # 3 days old (expected in CHOW)
            application_date="2026-02-01",
            is_change_of_ownership=True,
            primary_address=addr,
            disclosed_persons=[
                PersonDisclosure(
                    first_name="Alex",
                    last_name="Buyer",
                    ssn_last4="3344",
                    date_of_birth="1979-05-12",
                    role="OWNER",
                    direct_ownership_pct=1.0,
                    has_criminal_record_disclosed=True,
                )
            ],
        )
        vel_ev = tracker.record_and_evaluate(app)
        self.assertFalse(any(e.code == "M4-ANOMALY-RAPID-FORMATION" for e in vel_ev))

        l1_dec = self.l1_engine.evaluate_application(app)
        self.assertEqual(l1_dec.status, DecisionStatus.REVIEW_REQUIRED)
        self.assertTrue(any(e.code == "L1-DISCLOSED-CRIMINAL-RECORD" for e in l1_dec.verified_facts))

    def test_beacon_scenario3_exact_calibrated_score_without_double_counting(self):
        """BEACON (Scenario 3) deduplicates shared EFT and suppresses redundant PageRank (~80.3)."""
        from ebv_detector.synthetic.generator import SyntheticEBVGenerator

        ds = SyntheticEBVGenerator(seed=42).generate_benchmark_suite()
        cp1 = Checkpoint1Pipeline(self.config, ds.seed_records)
        beacon = next(a for a in ds.applications if "BEACON" in a.legal_business_name.upper())
        pkg = cp1.evaluate_application(beacon)

        self.assertEqual(pkg.layer1_decision.status, DecisionStatus.GROUNDS_PENDING_HUMAN_DECISION)
        self.assertAlmostEqual(pkg.advisory_score.priority_score, 80.3, places=1)
        self.assertAlmostEqual(
            sum(pkg.advisory_score.feature_contributions.values()),
            pkg.advisory_score.priority_score,
            places=1
        )
        self.assertFalse(any("PPR" in e.code for e in pkg.advisory_score.evidence_items))

    def test_api_server_real_world_clean_claims_and_null_enrollment_payloads(self):
        """API handles null/missing optional sections safely and does not falsify clean 8h workdays."""
        from ebv_detector.api.server import EBVServer

        srv = EBVServer()

        # Clean 8h workday with $80k paid should NOT falsely trigger impossible hours or payment suspension
        claims_res = srv.evaluate_claims({
            "npi": "1900099901",
            "service_category": "BEHAVIORAL_HEALTH",
            "months_enrolled": 3,
            "total_paid_30d": 80000,
            "max_daily_hours": 8.0,
            "inpatient_overlaps": 0,
            "post_mortem_claims": 0,
            "migrating_patients": 0,
        })
        self.assertFalse(claims_res["trigger_payment_suspension"])
        self.assertEqual(claims_res["ramp_result"]["impossible_hours_count"], 0)
        self.assertAlmostEqual(claims_res["ramp_result"]["total_paid_30d"], 80000.0, places=2)

        # Null eft_account and empty disclosed_persons on two different apps must NOT create bogus EFT-_ collision
        r1 = srv.evaluate_enrollment({
            "legal_business_name": "Clean Clinic Alpha LLC",
            "npi": "1900099902",
            "tin": "56-7770001",
            "service_category": "FAMILY_PRACTICE",
            "formation_date": "2018-01-01",
            "primary_address": {
                "address_line1": "101 North Blvd", "city": "Raleigh", "state": "NC", "zip5": "27601"
            },
            "eft_account": None,
            "disclosed_persons": []
        })
        r2 = srv.evaluate_enrollment({
            "legal_business_name": "Clean Clinic Beta LLC",
            "npi": "1900099903",
            "tin": "56-7770002",
            "service_category": "FAMILY_PRACTICE",
            "formation_date": "2018-01-01",
            "primary_address": {
                "address_line1": "202 South Blvd", "city": "Durham", "state": "NC", "zip5": "27701"
            },
            "eft_account": None,
            "disclosed_persons": []
        })
        self.assertEqual(r1["advisory_score"]["priority_score"], 0.0)
        self.assertEqual(r2["advisory_score"]["priority_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
