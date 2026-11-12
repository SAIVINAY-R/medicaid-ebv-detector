"""Unit tests for EBVServer API logic without requiring network sockets."""

import unittest

from ebv_detector.api.server import EBVServer, STATIC_DIR


class TestAPIServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = EBVServer()

    def test_static_index_html_exists(self):
        index_file = STATIC_DIR / "index.html"
        self.assertTrue(index_file.exists())
        content = index_file.read_text(encoding="utf-8")
        self.assertIn("Medicaid Provider Fraud Intelligence", content)
        self.assertIn("enrollment-form", content)

    def test_api_scenarios(self):
        data = self.server.get_sample_scenarios()
        self.assertIn("control", data)
        self.assertIn("scenario1", data)
        self.assertIn("scenario2", data)
        self.assertIn("scenario3", data)
        self.assertIn("scenario4", data)

    def test_api_seeds(self):
        seeds = self.server.dataset.seed_records
        self.assertGreater(len(seeds), 0)
        types = {s.identifier_type for s in seeds}
        self.assertIn("TIN", types)
        self.assertIn("EFT", types)

    def test_api_evaluate_enrollment_clean_provider(self):
        payload = {
            "legal_business_name": "Triangle Family Practice PLLC",
            "npi": "1900000001",
            "tin": "56-1122334",
            "service_category": "FAMILY_PRACTICE",
            "formation_date": "2015-01-01",
            "application_date": "2026-02-01",
            "phone": "919-555-1212",
            "primary_address": {
                "address_line1": "100 Medical Plaza",
                "city": "Raleigh",
                "state": "NC",
                "zip5": "27601",
                "is_cmra": False,
                "rdi": "COMMERCIAL"
            },
            "eft_account": {
                "routing_number": "053100099",
                "account_number": "1122334455"
            },
            "disclosed_persons": [{
                "first_name": "John",
                "last_name": "Doe",
                "role": "OWNER",
                "direct_ownership_pct": 1.0,
                "ssn_last4": "1234",
                "date_of_birth": "1975-01-01"
            }]
        }
        res = self.server.evaluate_enrollment(payload)
        self.assertEqual(res["layer1_decision"]["status"], "AUTO_APPROVE_ELIGIBLE")
        self.assertTrue(res["layer1_decision"]["can_auto_approve"])
        self.assertEqual(res["advisory_score"]["composite_risk_level"], "LOW")

    def test_api_evaluate_enrollment_cmra_mailbox_fraud(self):
        # Scenario 1 Mailbox store shell
        payload = {
            "legal_business_name": "Apex Autism Shell LLC",
            "npi": "1900000099",
            "tin": "56-9999999",
            "service_category": "AUTISM_CLINIC_FACILITY",
            "formation_date": "2026-01-25",
            "application_date": "2026-02-01",
            "primary_address": {
                "address_line1": "1204 Main St",
                "address_line2": "PMB 402",
                "city": "Raleigh",
                "state": "NC",
                "zip5": "27601",
                "is_cmra": True,
                "is_virtual_mailbox": True
            }
        }
        res = self.server.evaluate_enrollment(payload)
        self.assertEqual(res["layer1_decision"]["status"], "DENIED_GROUNDS")
        self.assertFalse(res["layer1_decision"]["can_auto_approve"])
        self.assertIn(res["advisory_score"]["composite_risk_level"], ("HIGH", "CRITICAL"))

    def test_api_evaluate_claims_surveillance(self):
        payload = {
            "npi": "1900000056",
            "service_category": "BEHAVIORAL_HEALTH",
            "months_enrolled": 3,
            "total_paid_30d": 250000,
            "max_daily_hours": 36,
            "inpatient_overlaps": 3,
            "post_mortem_claims": 2,
            "migrating_patients": 20
        }
        res = self.server.evaluate_claims(payload)
        self.assertTrue(res["trigger_payment_suspension"])
        self.assertTrue(res["trigger_unannounced_site_visit"])


if __name__ == "__main__":
    unittest.main()
