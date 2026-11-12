"""Unit tests for M2 Address Risk rules, clustering allowlist, and M4 MinHash template detection."""

import unittest

from ebv_detector.address.clustering import AddressClusterEngine
from ebv_detector.address.rules import evaluate_address_rules
from ebv_detector.anomaly.template import MinHashTemplateDetector
from ebv_detector.models.entities import AddressRecord, ProviderApplication


class TestAddressAndTemplates(unittest.TestCase):

    def test_cmra_and_po_box_prohibitions(self):
        # CMRA address
        cmra_addr = AddressRecord(
            address_line1="1204 Main St",
            address_line2="PMB 500",
            city="Raleigh",
            state="NC",
            zip5="27601",
            is_cmra=True
        )
        ev_items, is_fatal = evaluate_address_rules(cmra_addr, "BEHAVIORAL_HEALTH")
        self.assertTrue(is_fatal)
        self.assertTrue(any(e.code == "L1-ADDR-CMRA-PROHIBITED" for e in ev_items))

        # PO Box address
        pobox_addr = AddressRecord(
            address_line1="PO Box 1234",
            city="Raleigh",
            state="NC",
            zip5="27601",
            is_po_box=True
        )
        ev_items2, is_fatal2 = evaluate_address_rules(pobox_addr, "FAMILY_PRACTICE")
        self.assertTrue(is_fatal2)
        self.assertTrue(any(e.code == "L1-ADDR-POBOX-PROHIBITED" for e in ev_items2))

    def test_facility_service_at_residential_location(self):
        # Adult Day Care (facility-based) declared at residential address
        res_addr = AddressRecord(
            address_line1="45 Elm Street",
            city="Raleigh",
            state="NC",
            zip5="27601",
            rdi="RESIDENTIAL"
        )
        ev_items, is_fatal = evaluate_address_rules(res_addr, "ADULT_DAY_CARE")
        self.assertTrue(is_fatal)
        self.assertTrue(any(e.code == "L1-ADDR-FACILITY-RESIDENTIAL-MISMATCH" for e in ev_items))

    def test_address_clustering_and_fqhc_allowlist(self):
        engine = AddressClusterEngine()
        
        # High density commercial plaza with 4 unrelated providers
        plaza_addr = AddressRecord(
            address_line1="500 Executive Center Dr",
            city="Raleigh",
            state="NC",
            zip5="27601"
        )
        for i in range(3):
            app = ProviderApplication(
                application_id=f"APP-CLUSTER-{i}",
                npi=f"19000000{i}",
                legal_business_name=f"Clinic {i}",
                tin=f"56-00000{i}",
                primary_address=plaza_addr
            )
            engine.register_provider(app)

        # 4th application at same address triggers density flag
        app4 = ProviderApplication(
            application_id="APP-CLUSTER-3",
            npi="190000003",
            legal_business_name="Clinic 3",
            tin="56-000003",
            primary_address=plaza_addr
        )
        density_ev = engine.evaluate_density(app4)
        self.assertTrue(any(e.code == "M2-ADDR-HIGH-DENSITY-CLUSTER" for e in density_ev))

        # FQHC allowlist override
        fqhc_addr = AddressRecord(
            address_line1="500 Executive Center Dr",
            city="Raleigh",
            state="NC",
            zip5="27601",
            facility_type="FQHC"
        )
        app_fqhc = ProviderApplication(
            application_id="APP-FQHC",
            npi="190000099",
            legal_business_name="Community Health FQHC",
            tin="56-0000099",
            primary_address=fqhc_addr
        )
        allowlist_ev = engine.evaluate_density(app_fqhc)
        self.assertTrue(any(e.code == "M2-ADDR-ALLOWLIST-EXEMPT" for e in allowlist_ev))

    def test_minhash_policy_manual_template_detection(self):
        detector = MinHashTemplateDetector()
        
        manual_original = """
        CLINICAL COMPLIANCE AND OPERATING POLICY: AUTISM THERAPY SERVICES.
        SECTION 1: CLINICAL OVERSIGHT AND BEHAVIOR ANALYST DIRECT SUPERVISION.
        Direct supervision must be conducted weekly by a Board Certified Behavior Analyst.
        SECTION 2: INDIVIDUALIZED BEHAVIOR TREATMENT PLANS.
        Treatment plans must define observable, measurable behavioral targets with baseline data.
        """
        
        # Slightly perturbed copy-paste template
        manual_clone = """
        CLINICAL COMPLIANCE AND OPERATING POLICY: AUTISM THERAPY SERVICES.
        SECTION 1: CLINICAL OVERSIGHT AND BEHAVIOR ANALYST DIRECT SUPERVISION.
        Direct supervision must be conducted weekly by a Board Certified Behavior Analyst.
        SECTION 2: INDIVIDUALIZED BEHAVIOR TREATMENT PLANS.
        Treatment plans must define observable, measurable behavioral targets with baseline documentation.
        """

        app1 = ProviderApplication(
            application_id="APP-TEMPLATE-1",
            npi="1111111111",
            legal_business_name="First Autism Care LLC",
            tin="11-1111111",
            policy_manual_text=manual_original
        )
        detector.register_and_evaluate(app1)

        app2 = ProviderApplication(
            application_id="APP-TEMPLATE-2",
            npi="2222222222",
            legal_business_name="Second Autism Care LLC",
            tin="22-2222222",
            policy_manual_text=manual_clone
        )
        ev_items = detector.register_and_evaluate(app2)
        self.assertTrue(any(e.code == "M4-TEMPLATE-POLICY-CLONE" for e in ev_items))


if __name__ == "__main__":
    unittest.main()

