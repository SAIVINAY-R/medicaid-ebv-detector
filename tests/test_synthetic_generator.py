"""Unit tests for Spike S3 Synthetic EBV Generator coverage of all §2.4 red flags."""

import unittest

from ebv_detector.synthetic.generator import SyntheticEBVGenerator


class TestSyntheticGenerator(unittest.TestCase):

    def setUp(self):
        self.generator = SyntheticEBVGenerator(seed=101)
        self.dataset = self.generator.generate_benchmark_suite()

    def test_generator_structure_and_counts(self):
        self.assertGreater(len(self.dataset.applications), 20)
        self.assertGreater(len(self.dataset.seed_records), 5)
        self.assertGreater(len(self.dataset.claims), 200)
        self.assertGreater(len(self.dataset.ownership_edges), 0)

    def test_lifecycle_red_flag_coverage(self):
        # Verify Scenario 1: CMRA Mailbox Shell
        cmra_apps = [a for a in self.dataset.applications if a.primary_address and a.primary_address.is_cmra]
        self.assertGreater(len(cmra_apps), 0)
        s1 = cmra_apps[0]
        self.assertLess(s1.days_since_formation, 30)

        # Verify Scenario 2: Indirect Ownership Linkage to Excluded Seed
        holding_edges = [e for e in self.dataset.ownership_edges if "HOLDING" in e.parent_entity_id or "HOLDING" in e.child_entity_id]
        self.assertGreater(len(holding_edges), 0)

        # Verify Scenario 3: Shared EFT Re-entry
        eft_keys = [a.eft_account.account_key for a in self.dataset.applications if a.eft_account]
        seed_eft_values = [s.identifier_value for s in self.dataset.seed_records if s.identifier_type == "EFT"]
        self.assertTrue(any(k in seed_eft_values for k in eft_keys))

        # Verify Scenario 4: Multi-Entity Clinician Sharing & Templated Policy Text
        templated_apps = [a for a in self.dataset.applications if a.policy_manual_text and "POLICY AND OPERATING MANUAL" in a.policy_manual_text]
        self.assertGreaterEqual(len(templated_apps), 3)

        # Verify Scenario 5: Ramp Claims with Impossible Hours & Post-Mortem
        deceased_claims = [c for c in self.dataset.claims if c.beneficiary_deceased]
        inpatient_claims = [c for c in self.dataset.claims if c.inpatient_overlap]
        self.assertGreater(len(deceased_claims), 0)
        self.assertGreater(len(inpatient_claims), 0)


if __name__ == "__main__":
    unittest.main()

