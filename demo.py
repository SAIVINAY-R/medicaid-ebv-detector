#!/usr/bin/env python3
"""Interactive End-to-End Demonstration CLI for the EBV Fraud Detection System.

Demonstrates:
1. Real-time Checkpoint 1 evaluation of clean providers vs EBV sham rings
2. M1 Fellegi-Sunter entity resolution & near-match triage
3. M3 Recursive CTE graph traversal & GR-4 indirect ownership calculation
4. M2 Address hygiene (DATA-039 prohibitions) & M4 MinHash template cloning
5. Checkpoint 2 MMIS claims surveillance (zero-to-millions ramp & impossible hours)
6. Checkpoint 2 bipartite patient cohort migration from suspended providers
7. WORM audit trail verification and explainable reviewer evidence panels
8. Full benchmark suite execution with precision, recall, and latency metrics
"""

import json
import sys
import time

from ebv_detector.evaluation.harness import EvaluationHarness
from ebv_detector.pipeline.checkpoint1 import Checkpoint1Pipeline
from ebv_detector.pipeline.checkpoint2 import Checkpoint2Pipeline
from ebv_detector.synthetic.generator import SyntheticEBVGenerator


def print_banner():
    print("""
================================================================================
   MEDICAID PROVIDER ENROLLMENT FRAUD INTELLIGENCE SYSTEM (EBV DETECTOR)
   Complies with ADR-0001, 42 CFR 455, and NC RFP Attachment C Section 3
================================================================================
""")


def demonstrate_scenario_1_mailbox_shell(cp1: Checkpoint1Pipeline, app):
    print("\n" + "-" * 80)
    print(" [SCENARIO 1] RAPID FORMATION & CMRA MAILBOX STORE FOR FACILITY SERVICE")
    print("-" * 80)
    print(f"Provider:        {app.legal_business_name} (NPI: {app.npi})")
    print(f"Service Type:    {app.service_category}")
    print(f"Address:         {app.primary_address.normalized_single_line}")
    print(f"Formation Date:  {app.formation_date} ({app.days_since_formation} days prior to application)")
    print(f"Melissa Flags:   CMRA={app.primary_address.is_cmra}, PMB={app.primary_address.is_pmb}")

    res = cp1.evaluate_application(app)

    print("\n>>> EVALUATION OUTPUT:")
    print(f"  Layer 1 Decision Status:     {res.layer1_decision.status.value}")
    print(f"  Assigned ACA Risk Tier:      {res.layer1_decision.assigned_aca_risk_tier}")
    print(f"  Statutory Denial Grounds:    {res.layer1_decision.statutory_grounds}")
    print(f"  Advisory Priority Score:     {res.advisory_score.priority_score} / 100.0 [{res.advisory_score.composite_risk_level}]")
    print(f"  Summary:                     {res.advisory_score.explanation_summary}")
    print("\n>>> SURFACED EVIDENCE ITEMS:")
    for ev in res.advisory_score.evidence_items:
        print(f"  * [{ev.source_layer}] ({ev.severity.value}) {ev.code}: {ev.description}")


def demonstrate_scenario_2_indirect_ownership(cp1: Checkpoint1Pipeline, app):
    print("\n" + "-" * 80)
    print(" [SCENARIO 2] STRAW NOMINEE & GR-4 INDIRECT OWNERSHIP LINK TO EXCLUDED FELON")
    print("-" * 80)
    print(f"Provider:        {app.legal_business_name} (NPI: {app.npi})")
    print(f"Disclosed Owner: {app.disclosed_persons[0].full_name} (Age: 23, Disclosed Pct: 20%)")
    print("Holding Org:     ORG-HOLDING-CAROLINA-HEALTH (Holds 80% Direct Ownership)")

    res = cp1.evaluate_application(app)

    print("\n>>> GR-4 RECURSIVE OWNERSHIP CALCULATION:")
    for ind in res.indirect_ownership:
        print(f"  * Upstream Entity: {ind.upstream_entity_id}")
        print(f"    Effective Indirect Ownership: {ind.effective_indirect_pct:.1%} (Statutory Threshold >= 5.0%: {ind.meets_5pct_statutory_threshold})")
        print(f"    Is Excluded Seed: {ind.is_upstream_excluded_seed} ({ind.seed_reason})")

    print("\n>>> EVALUATION OUTPUT:")
    print(f"  Layer 1 Decision Status:     {res.layer1_decision.status.value}")
    print(f"  Statutory Grounds:           {res.layer1_decision.statutory_grounds}")
    print(f"  Advisory Priority Score:     {res.advisory_score.priority_score} / 100.0 [{res.advisory_score.composite_risk_level}]")


def demonstrate_scenario_3_shared_eft_reentry(cp1: Checkpoint1Pipeline, app):
    print("\n" + "-" * 80)
    print(" [SCENARIO 3] PHOENIX RE-ENTRY WITH MATCHING EFT OF TERMINATED PROVIDER")
    print("-" * 80)
    print(f"Provider:        {app.legal_business_name} (NPI: {app.npi})")
    print(f"EFT Bank Acct:   Routing {app.eft_account.routing_number}, Account {app.eft_account.account_number}")

    res = cp1.evaluate_application(app)

    print("\n>>> EVALUATION OUTPUT:")
    print(f"  Layer 1 Decision Status:     {res.layer1_decision.status.value}")
    print(f"  Statutory Grounds:           {res.layer1_decision.statutory_grounds}")
    print(f"  Advisory Priority Score:     {res.advisory_score.priority_score} / 100.0 [{res.advisory_score.composite_risk_level}]")
    for ev in res.advisory_score.evidence_items:
        if "EFT" in ev.code:
            print(f"  * Verified Fact: {ev.description}")


def demonstrate_scenario_5_billing_ramp(cp2: Checkpoint2Pipeline, app, claims):
    print("\n" + "-" * 80)
    print(" [SCENARIO 5] CHECKPOINT 2 MMIS SURVEILLANCE: ZERO-TO-MILLIONS RAMP & IMPOSSIBLE HOURS")
    print("-" * 80)
    print(f"Provider:        {app.legal_business_name} (NPI: {app.npi})")
    print(f"Enrollment Age:  Month 3")
    print(f"Total Claims:    {len(claims)} claims analyzed")

    sig = cp2.evaluate_provider_early_billing(app, months_enrolled=3, claims=claims)
    ramp = sig.ramp_result

    print("\n>>> CLAIMS SURVEILLANCE METRICS:")
    print(f"  Total Paid (30 Days):        ${ramp.total_paid_30d:,.2f}")
    print(f"  Peer Cohort Median (30d):    ${ramp.cohort_median_paid_30d:,.2f}")
    print(f"  Robust Z-Score:              +{ramp.robust_z_score:.1f}x above cohort normal")
    print(f"  Impossible Daily Hours:      {ramp.impossible_hours_count} clinician-days > 24 hours")
    print(f"  Hospital Inpatient Overlaps: {ramp.inpatient_overlap_count} claims")
    print(f"  Post-Mortem Claims:          {ramp.deceased_overlap_count} claims")
    print(f"\n>>> ACTION TRIGGERED IN CPMM:")
    print(f"  Recommended Action:          {sig.recommended_cpmm_action}")
    print(f"  Payment Suspension Hold:     {sig.trigger_payment_suspension} (42 CFR 455.23 CAF)")
    print(f"  Unannounced Site Visit:      {sig.trigger_unannounced_site_visit}")


def demonstrate_scenario_6_patient_migration(cp2: Checkpoint2Pipeline, app):
    print("\n" + "-" * 80)
    print(" [SCENARIO 6] BIPARTITE PATIENT-SHARING NETWORK: COHORT MIGRATION FROM SUSPENDED PROVIDER")
    print("-" * 80)
    print(f"Newly Enrolled:  {app.legal_business_name} (NPI: {app.npi})")
    print("Prior Provider:  Suspended NPI 1487654321 (State Medicaid CAF Suspension)")

    mig_ev = cp2.migration_detector.evaluate_patient_migration(app.npi)
    print("\n>>> BIPARTITE OVERLAP DETECTION:")
    for ev in mig_ev:
        print(f"  * {ev.description}")
        print(f"    Details: Shared Patients = {ev.details.get('shared_patient_count')}, Jaccard = {ev.details.get('jaccard_similarity')}")


def main():
    print_banner()
    print("Initializing Synthetic EBV Benchmark Suite...")
    generator = SyntheticEBVGenerator(seed=42)
    dataset = generator.generate_benchmark_suite()
    print(f"Generated {len(dataset.applications)} provider applications ({len(dataset.seed_records)} known-bad seeds).")

    # Initialize Checkpoint 1 and Checkpoint 2
    cp1 = Checkpoint1Pipeline(seeds=dataset.seed_records)
    cp1.ingest_ownership_edges(dataset.ownership_edges)

    cp2 = Checkpoint2Pipeline()
    for s in dataset.seed_records:
        if s.identifier_type == "NPI":
            cp2.register_sanctioned_provider(s.identifier_value)
    cp2.ingest_claims(dataset.claims)

    # Index claims
    claims_by_npi = {}
    for c in dataset.claims:
        claims_by_npi.setdefault(c.billing_provider_npi, []).append(c)

    # 1. Demonstrate Legitimate Clean Provider
    clean_app = dataset.applications[0]
    print("\n" + "-" * 80)
    print(" [CONTROL] LEGITIMATE COMMUNITY PROVIDER SCREENING")
    print("-" * 80)
    print(f"Provider:        {clean_app.legal_business_name} (NPI: {clean_app.npi})")
    print(f"Service Type:    {clean_app.service_category}")
    print(f"Formation Date:  {clean_app.formation_date} (Established legitimate clinic)")
    res_clean = cp1.evaluate_application(clean_app)
    print(f"  Layer 1 Status:              {res_clean.layer1_decision.status.value}")
    print(f"  Can Auto-Approve:            {res_clean.layer1_decision.can_auto_approve}")
    print(f"  Priority Score:              {res_clean.advisory_score.priority_score} / 100.0 [{res_clean.advisory_score.composite_risk_level}]")
    print(f"  WORM Audit Hash:             {res_clean.audit_record.audit_hash[:24]}...")

    # 2. Demonstrate Fraud Scenarios
    app_s1 = [a for a in dataset.applications if a.ground_truth_label == "EBV_FRAUD" and a.primary_address and a.primary_address.is_cmra][0]
    demonstrate_scenario_1_mailbox_shell(cp1, app_s1)

    app_s2 = [a for a in dataset.applications if "HORIZON" in a.legal_business_name][0]
    demonstrate_scenario_2_indirect_ownership(cp1, app_s2)

    app_s3 = [a for a in dataset.applications if "BEACON" in a.legal_business_name][0]
    demonstrate_scenario_3_shared_eft_reentry(cp1, app_s3)

    app_s5 = [a for a in dataset.applications if "TRIANGLE CRISIS" in a.legal_business_name][0]
    demonstrate_scenario_5_billing_ramp(cp2, app_s5, claims_by_npi.get(app_s5.npi, []))

    app_s6 = [a for a in dataset.applications if "NEW DAWN" in a.legal_business_name][0]
    demonstrate_scenario_6_patient_migration(cp2, app_s6)

    # 3. Run Benchmark Suite
    print("\n" + "=" * 80)
    print(" RUNNING FULL BENCHMARK SUITE ACROSS ALL COHORTS...")
    print("=" * 80)
    harness = EvaluationHarness()
    report = harness.run_benchmark(dataset)
    report.print_summary()


if __name__ == "__main__":
    main()

