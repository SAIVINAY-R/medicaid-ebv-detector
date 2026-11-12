"""Enroll, Bill, Vanish (EBV) Fraud Detection System for Medicaid Provider Enrollment.

Modular, audit-compliant implementation covering:
- Layer 1 Deterministic Screening Rules & ACA Categorical Tiers
- M1 Fellegi-Sunter Entity Resolution & Near-Match Queue
- M2 Address Risk & Co-Location Density Rules
- M3 Relationship Graph Analytics (Recursive CTE, GR-4 Indirect Ownership, Supernode Damping, Seed Risk Propagation)
- M4 Application Anomaly & MinHash Template Detection
- Checkpoint 2 Billing Ramps, Hard Billing Edits & Patient Migration
- WORM Audit Trail & Explainable Reviewer Evidence Panels
"""

__version__ = "1.0.0-phase0"

