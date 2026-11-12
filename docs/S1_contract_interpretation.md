# Phase 0 Research Spike S1 — Contract & Data-Use Legal Position Memo

| Metadata | Details |
|---|---|
| **Author** | Senior ML Engineer / Legal-Architecture Pair |
| **Date** | 2026-11-11 |
| **Governing Contract** | NCDHHS RFP #30-2026-017-DHB, Attachment C Section 3 |
| **Applicable Standards** | NIST AI RMF 1.0, 42 CFR 455.450, ADR-0001 |
| **Status** | **Approved Architectural Position** |

---

## 1. Executive Summary

Medicaid fraud detection systems operate under strict state and federal procurement constraints. Specifically, Attachment C Section 3 of the North Carolina Department of Health and Human Services (NCDHHS) contract imposes explicit restrictions on using "State Data" for model training and mandates that all artificial intelligence systems comply with "American AI" provenance requirements.

This memorandum establishes the formal architectural and engineering boundaries to guarantee 100% compliance while delivering high-accuracy fraud prevention for "Enroll, Bill, Vanish" (EBV) schemes.

---

## 2. Key Clause Interpretations

### 2.1 Section 3(e)(ii) — Prohibition on Model Training on State Data
> *"The State Data shall not be used to train, fine-tune, or otherwise improve any LLM or other machine learning or AI models..."*

#### Interpretation & Engineering Guidance:
1. **Supervised Learning Prohibition**: No supervised neural network, gradient-boosted decision tree (LightGBM/XGBoost), or LLM fine-tuning may be trained on North Carolina Medicaid claims, disclosures, or enrollment applications.
2. **Unsupervised vs. Parametric Model Fitting**: Fitting statistical models whose internal parameters adapt based on State Data distributions (e.g., Expectation-Maximization in Splink/Fellegi-Sunter, Isolation Forest tree splits, DBSCAN epsilon clustering) presents contract risk.
   - **Resolution**: Phase 1 and Phase 2 implementations must use **expert-calibrated, rule-based log-likelihood weights** in the Fellegi-Sunter comparator, and deterministic spatial thresholds in address clustering.
   - Dynamic parameter re-fitting on live State Data is barred unless explicit written authorization is granted by NCDHHS PSO.
3. **Synthetic Data Boundaries**: Under Section 1 Definitions, "State Data" includes all Data Inputs and derivative Data Outputs, explicitly including synthetic data if generated from State Data.
   - **Resolution**: All synthetic data generators (including `SyntheticEBVGenerator`) are engineered **exclusively from publicly available federal indictments (DOJ 2026 takedowns), CMS public statistics, and federal statutes (42 CFR Part 455)**. No state-confidential records or operational extracts are used to seed the generator.

---

### 2.2 Section 3(e)(i) — "American AI Systems" & Component Provenance
> *"Disclose every AI system; 'American AI Systems' only; components 'developed, or controlled by non-U.S. entities' prohibited."*

#### Architectural Impact:
- Many popular open-source packages originated abroad:
  - `Splink`: Developed by the UK Ministry of Justice (United Kingdom).
  - `scikit-learn`: Originated at INRIA (France).
  - `DuckDB`: Developed by DuckDB Labs (The Netherlands).
- **Compliance Architecture**:
  - The core EBV detection platform is authored in clean, zero-dependency Python utilizing standard library primitives (`sqlite3`, `hashlib`, `math`, `collections`).
  - Graph traversal uses standard ANSI SQL Common Table Expressions (`WITH RECURSIVE`) natively within SQLite / Azure SQL / PostgreSQL.
  - This eliminates non-U.S. third-party open-source provenance risks, dependency supply-chain vulnerabilities, and licensing disputes.

---

### 2.3 Section 4(d)–(e) — "Eyes-Off" Handling & Multi-State Segregation
> *"Human access to State Data logged, justified, minimum necessary; State Data segregated, not commingled with other customers' data."*

#### Operational Requirements:
1. **No Cross-State Pooling**: North Carolina, North Dakota, and New Hampshire tenant data must remain strictly segregated in distinct Dataverse / Azure ML environments.
2. **Seed Sharing Boundaries**:
   - Federal public seeds (OIG LEIE, SAM.gov, PECOS, DMF) may be shared across all states.
   - State-specific CAF payment suspensions and for-cause terminations may **not** be pooled across state boundaries without an executed Interstate Data-Sharing Agreement.

---

### 2.4 Section 5 — NIST AI RMF Documentation & Human Overrides

1. **System Impact Tier**: The EBV detection system is classified as **Assistive (Advisory)**:
   - ML and heuristic scores **never** deny an enrollment, terminate a provider, or assign an ACA categorical risk tier (42 CFR 455.450).
   - Only human adjudicators and authorized Program Integrity officers possess statutory authority to issue adverse determinations on verified grounds (42 CFR 455.416).
2. **Audit & Replay**:
   - Every inference produces a SHA-256 fingerprint of the input feature vector (`featureVectorSha256`), logged to an immutable WORM (Write Once, Read Many) audit log.
   - Reviewer overrides require mandatory justification comments and are cryptographically chained to maintain an untampered audit log.

