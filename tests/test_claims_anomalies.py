"""Unit tests for Checkpoint 2 billing rules, robust z-scores, and patient migration."""

import unittest

from ebv_detector.claims.billing_rules import evaluate_hard_billing_edits
from ebv_detector.claims.patient_sharing import PatientMigrationDetector
from ebv_detector.claims.ramp_detector import BillingRampDetector
from ebv_detector.models.entities import ClaimRecord


class TestClaimsAnomalies(unittest.TestCase):

    def test_impossible_hours_and_inpatient_edits(self):
        claims = [
            # 28 hours billed by one clinician on same day
            ClaimRecord(
                claim_id="CLM-01",
                billing_provider_npi="1900000001",
                rendering_clinician_npi="CLIN-1",
                beneficiary_id="BEN-1",
                service_date="2026-03-01",
                procedure_code="H0015",
                units_billed=60.0,
                paid_amount=1200.0,
                service_hours=15.0
            ),
            ClaimRecord(
                claim_id="CLM-02",
                billing_provider_npi="1900000001",
                rendering_clinician_npi="CLIN-1", # Same clinician, same date
                beneficiary_id="BEN-2",
                service_date="2026-03-01",
                procedure_code="H0015",
                units_billed=52.0,
                paid_amount=1000.0,
                service_hours=13.0
            ),
            # Inpatient overlap
            ClaimRecord(
                claim_id="CLM-03",
                billing_provider_npi="1900000001",
                rendering_clinician_npi="CLIN-2",
                beneficiary_id="BEN-3",
                service_date="2026-03-02",
                procedure_code="H0015",
                units_billed=4.0,
                paid_amount=100.0,
                service_hours=1.0,
                inpatient_overlap=True
            ),
            # Post-mortem claim
            ClaimRecord(
                claim_id="CLM-04",
                billing_provider_npi="1900000001",
                rendering_clinician_npi="CLIN-2",
                beneficiary_id="BEN-4",
                service_date="2026-03-03",
                procedure_code="H0015",
                units_billed=4.0,
                paid_amount=100.0,
                service_hours=1.0,
                beneficiary_deceased=True
            )
        ]

        ev_items, imp_hrs, inpat_cnt, post_cnt = evaluate_hard_billing_edits(claims)
        self.assertEqual(imp_hrs, 1)
        self.assertEqual(inpat_cnt, 1)
        self.assertEqual(post_cnt, 1)
        self.assertTrue(any(e.code == "CP2-BILLING-IMPOSSIBLE-HOURS" for e in ev_items))
        self.assertTrue(any(e.code == "CP2-BILLING-INPATIENT-OVERLAP" for e in ev_items))
        self.assertTrue(any(e.code == "CP2-BILLING-POST-MORTEM" for e in ev_items))

    def test_billing_ramp_detector(self):
        detector = BillingRampDetector()
        
        # Peer cohort median ~$75k
        for vol in [60000.0, 70000.0, 75000.0, 80000.0, 90000.0]:
            detector.register_peer_monthly_volume("BEHAVIORAL_HEALTH", 3, vol)

        # Sham provider billing $1.8M in month 3
        sham_claims = [
            ClaimRecord(
                claim_id=f"CLM-RAMP-{i}",
                billing_provider_npi="1900000099",
                rendering_clinician_npi="CLIN-99",
                beneficiary_id=f"BEN-{i}",
                service_date="2026-03-10",
                procedure_code="H0015",
                units_billed=10.0,
                paid_amount=180000.0, # Total across 10 claims = $1.8M
                service_hours=2.0
            ) for i in range(10)
        ]

        res = detector.evaluate_provider_claims("1900000099", "BEHAVIORAL_HEALTH", 3, sham_claims)
        self.assertTrue(res.is_ramp_anomaly)
        self.assertGreater(res.robust_z_score, 10.0)
        self.assertTrue(any(e.code == "CP2-RAMP-ABNORMAL-VELOCITY" for e in res.evidence_items))

    def test_patient_cohort_migration(self):
        detector = PatientMigrationDetector()
        suspended_npi = "1487654321"
        new_npi = "1900000088"
        detector.register_sanctioned_provider(suspended_npi)

        # 25 patients migrate en masse from suspended provider to new provider
        claims = []
        for i in range(25):
            b_id = f"BEN-MIG-{i}"
            claims.append(ClaimRecord(
                claim_id=f"C-OLD-{i}",
                billing_provider_npi=suspended_npi,
                rendering_clinician_npi="DOC-OLD",
                beneficiary_id=b_id,
                service_date="2026-01-10",
                procedure_code="H0015",
                units_billed=4.0,
                paid_amount=100.0,
                service_hours=1.0
            ))
            claims.append(ClaimRecord(
                claim_id=f"C-NEW-{i}",
                billing_provider_npi=new_npi,
                rendering_clinician_npi="DOC-NEW",
                beneficiary_id=b_id,
                service_date="2026-02-15",
                procedure_code="H0015",
                units_billed=4.0,
                paid_amount=100.0,
                service_hours=1.0
            ))

        detector.ingest_claims(claims)
        mig_ev = detector.evaluate_patient_migration(new_npi)
        self.assertEqual(len(mig_ev), 1)
        self.assertEqual(mig_ev[0].code, "CP2-PATIENT-COHORT-MIGRATION")


if __name__ == "__main__":
    unittest.main()
