# Phase 0 Research Spike S8 — Program Integrity Label & Disposition Taxonomy

| Taxonomy Metadata | Specification |
|---|---|
| **Author** | Senior ML Engineer |
| **Date** | 2026-11-11 |
| **Stakeholders** | State Program Integrity (PI), MFCU, CPMM Architecture |
| **Status** | **Approved Reference Model** |

---

## 1. Problem Statement: Positive-Unlabeled (PU) Reality

In healthcare fraud analytics, ground truth labels are **asymmetric and delayed**:
1. **Confirmed Bad**: When Program Integrity or the Attorney General (MFCU) concludes an investigation with an adverse disposition (CAF payment suspension, termination for cause, exclusion, criminal conviction), the entity is definitively positive (`LABEL = 1`).
2. **Unknown is NOT "Good"**: An enrolled provider with no investigations or flags is simply **uninspected**, not guaranteed to be legitimate. Many EBV sham providers operate for months or years before discovery.
3. **Temporal Lag**: Investigations often take 12 to 24 months to reach a formal disposition. Features must always be calculated **strictly as of the application or transaction date (Point-in-Time)** to prevent lookahead data leakage.

---

## 2. Standardized Program Integrity Disposition Taxonomy

| Disposition Code | Category | Statutory Reference | Label Class | Permitted System Action |
|---|---|---|---|---|
| `DISP-CAF-SUSPEND` | Credible Allegation of Fraud Payment Suspension | 42 CFR 455.23 | Confirmed Adverse (Bad) | Add as Graph Seed; Trigger Checkpoint 2 Patient Migration Tracking |
| `DISP-TERM-CAUSE` | For-Cause Provider Termination | 42 CFR 455.416 | Confirmed Adverse (Bad) | Mandatory Layer 1 Denial Ground; Add to Seed Bank |
| `DISP-EXCL-LEIE` | OIG LEIE / SAM Federal Exclusion | 42 CFR 455.436 | Confirmed Adverse (Bad) | Mandatory Denial Ground under 455.416(c); Graph Seed Node |
| `DISP-MFCU-ACCEPT` | State AG MFCU Criminal Referral Accepted | 42 CFR 455.21 | Confirmed Adverse (Bad) | Immediate Escalation to High Risk; Hold Prepayment |
| `DISP-OVERPAY-DEM` | Overpayment Demand (> $100k, Fraud Suspected) | 42 CFR 455.450(e) | Confirmed Adverse (Bad) | Mandatory Elevation to High ACA Risk Tier; Add to Financial Edge |
| `DISP-SITE-FAILED` | Failed Unannounced Physical Site Visit | 42 CFR 455.432 / 455.416 | Confirmed Adverse (Bad) | Mandatory Denial Ground (Failed Site Visit); Flag Address as Sham |
| `DISP-CLINIC-VERIF` | Positive Site Visit & Operational Clinic Verified | 42 CFR 455.432 | Verified Legitimate (Good) | Down-weight Heuristic Anomalies; Record in Verified Registry |
| `DISP-ADMIN-CLOSED` | Withdrawn by Provider Prior to Site Visit | 42 CFR 455.416 | Suspicious / Censored | Retain Identifiers (EFT, Device, Phone) in M1 Watchlist |

---

## 3. Seed Conversion & Graph Ingestion Rules

1. **Seed Creation**: Any disposition in the `Confirmed Adverse` class immediately generates a `SeedRecord`.
2. **Deterministic Identifiers**:
   - The provider's NPI and TIN are registered as direct Layer 1 exclusion keys.
   - The provider's active EFT bank routing and account numbers are added to `_seeds_by_eft`.
   - The provider's disclosed 5%+ owners and managing employees are registered as `SSN4_DOB` seeds.
   - The provider's primary physical address is flagged as a high-risk location.
3. **Point-in-Time Graph Integrity**:
   - Every seed record carries an `effective_date`. Graph CTE traversals and Personalized PageRank queries only incorporate seeds that were effective as of the evaluated event date.
