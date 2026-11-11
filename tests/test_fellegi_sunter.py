"""Unit tests for M1 Fellegi-Sunter Probabilistic Record Linkage and Near-Match Queue."""

import unittest

from ebv_detector.entity_resolution.fellegi_sunter import FellegiSunterResolver, FieldWeightConfig
from ebv_detector.entity_resolution.near_match_queue import AdjudicationAction, NearMatchQueue
from ebv_detector.models.entities import AddressRecord, EFTAccount, PersonDisclosure, ProviderApplication


class TestFellegiSunter(unittest.TestCase):

    def setUp(self):
        self.resolver = FellegiSunterResolver()
        self.queue = NearMatchQueue()

    def test_log_likelihood_weight_calculation(self):
        # High discriminating identifier: m=0.98, u=0.00005
        cfg = FieldWeightConfig(m_prob=0.98, u_prob=0.00005)
        # Agreement weight should be high positive log2
        self.assertGreater(cfg.agree_weight, 14.0)
        # Disagreement weight should be negative log2
        self.assertLess(cfg.disagree_weight, -5.0)

    def test_near_match_queue_workflow(self):
        app_a = ProviderApplication(
            application_id="APP-001",
            npi="1111111111",
            legal_business_name="Apex Care Solutions LLC",
            tin="12-3456789",
            phone="919-555-1212",
            eft_account=EFTAccount(routing_number="053000196", account_number="123456")
        )
        app_b = ProviderApplication(
            application_id="APP-002",
            npi="2222222222",
            legal_business_name="Apex Care Solutions Inc", # Slight spelling variation
            tin="98-7654321",
            phone="919-555-1212", # Same phone
            eft_account=EFTAccount(routing_number="053000196", account_number="999999")
        )

        match_res = self.resolver.compare_pair(app_a, app_b, "phone_clean:9195551212")
        self.assertTrue(match_res.is_near_match or match_res.is_match)

        # Enqueue
        q_item = self.queue.enqueue(match_res)
        self.assertEqual(q_item.status, "PENDING")
        self.assertEqual(len(self.queue.get_pending_items()), 1)

        # Adjudicate LINK
        adjudicated = self.queue.adjudicate(
            item_id=q_item.item_id,
            action=AdjudicationAction.LINK,
            reviewer_id="REV-104",
            notes="Confirmed re-enrollment under new corporate suffix"
        )
        self.assertEqual(adjudicated.status, "ADJUDICATED")
        self.assertEqual(adjudicated.adjudicated_action, AdjudicationAction.LINK)
        self.assertTrue(self.queue.is_confirmed_link("APP-001", "APP-002"))


if __name__ == "__main__":
    unittest.main()
