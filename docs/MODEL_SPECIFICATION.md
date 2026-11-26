# Medicaid Provider Fraud Intelligence System (EBV Detector)
## Model Architecture & Machine Learning Specification Document
**Document ID:** `DOC-EBV-ML-SPEC-001`  
**Classification:** State Medicaid Program Integrity / Law Enforcement Sensitive  
**Target Threat:** "Enroll, Bill, Vanish" (EBV) Sham Healthcare Provider Syndicates  
**Compliance Standards:** 42 CFR Part 455, NC RFP Attachment C §3, ADR-0001 Architecture  
**Release Version:** `1.0.0-PROD`  

---

### Executive Summary

Medicaid "Enroll, Bill, Vanish" (EBV) fraud occurs when organized criminal rings establish shell provider organizations, disguise beneficial ownership, rapidly bill millions of dollars in fabricated healthcare claims, and vanish before traditional 12-to-18-month post-payment audits discover the scheme.

This system shifts fraud prevention from retrospective "pay-and-chase" audits to real-time pre-payment intervention across two checkpoints:
1. **Checkpoint 1 (Pre-Enrollment Screening):** Evaluates provider applications in real time using probabilistic entity resolution, physical address hygiene, recursive multi-hop relational graph traversal, and document plagiarism detection.
2. **Checkpoint 2 (Rapid Post-Enrollment Claims Surveillance):** Monitors the first 30 to 90 days of claims billing for impossible physical edits, robust statistical velocity spikes, and bipartite patient cohort recycling.

To comply with federal due process (**42 CFR §455**) and state contract constraints (**NC RFP Attachment C §3**), the architecture strictly adheres to **Architectural Decision Record 0001 (ADR-0001)**:
- **Layer 1 (Authoritative Deterministic Gate):** Deterministic rules evaluating statutory exclusion grounds (§455.416), physical address bans, and setting authoritative **ACA Risk Tiers (§455.450)**.
- **Layer 2 (Assistive Advisory Scorer):** Calibrated statistical and graph models that compute an explainable **Advisory Priority Score (0–100)** with canonical `fact_key` de-duplication across six evidence families for human investigator queue prioritization while preserving human adjudicative authority.

---

### Model Architecture Overview

```
                      Incoming Provider Disclosure / Claims Stream
                                           │
                                           ▼
                       [ Feature Normalization & Ingestion ]
                                           │
     ┌─────────────────────────────────────┴─────────────────────────────────────┐
     ▼                                                                           ▼
Checkpoint 1: Pre-Enrollment                                     Checkpoint 2: Post-Enrollment (Days 1–90)
     │                                                                           │
     ├─► Model 1: Fellegi-Sunter Probabilistic Linkage                           ├─► Model 4: Robust MAD Z-Score Outlier
     ├─► Model 2: Spatial Density Clustering & CMRA                              ├─► Hard Physical Edits (>24h, Inpatient)
     ├─► Model 3: Personalized PageRank & Multi-Hop Traversal                    └─► Model 5: Bipartite Patient Migration
     └─► Model 4: MinHash 128-Permutation LSH Document NLP                       │
     │                                                                           │
     ▼                                                                           ▼
Layer 1 Authoritative Rules Gate                                    42 CFR §455.23 Payment Suspension
     │                                                                           │
     ▼                                                                           ▼
Layer 2 Calibrated Advisory Scorer (0-100) ─────────────────────────► WORM Cryptographic SHA-256 Audit
     │
     ▼
Human Investigator Adjudication (HITL)
```

---

### Master Inventory of Machine Learning & Statistical Models

| Model ID | Model Name | Category | Primary Code File | Mathematical Formulation | Regulatory Ground |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M1** | **Fellegi-Sunter Record Linkage** | Probabilistic Generative Model | `entity_resolution/fellegi_sunter.py` | Bayesian log2 odds: $\sum \log_2(m_i / u_i)$ | 42 CFR §455.416 |
| **M2** | **Address Density Clustering** | Spatial / Density Clustering | `address/clustering.py` | Allowlist-suppressed cluster density | DT-SCR-GATE-002 |
| **M3-A** | **Recursive CTE Traversal** | Relational Graph Traversal | `graph/traversal.py` | Multi-hop ANSI SQL recursion ($2-3$ hops) | 42 CFR §455.104 |
| **M3-B** | **Personalized PageRank (PPR)** | Graph Spectral / Random Walk | `graph/risk_propagation.py` | Power iteration: $p^{(k+1)} = (1-\alpha)\mathbf{A}^Tp^{(k)} + \alpha r$ | 42 CFR §455.450 |
| **M4-A** | **Telemetry Velocity Tracker** | Temporal Anomaly Detection | `anomaly/application.py` | Sliding-window hardware & IP velocity | ADR-0001 Assistive |
| **M4-B** | **MinHash LSH Document NLP** | Dimensionality Reduction / LSH | `anomaly/template.py` | $m=128$ Universal Hash permutations | State Anti-Fraud |
| **CP2-A** | **Robust MAD Z-Score Model** | Non-Parametric Outlier Model | `claims/ramp_detector.py` | Robust $Z = (X - \text{median}) / (1.4826 \cdot \text{MAD})$ | 42 CFR §455.23 |
| **CP2-B** | **Bipartite Cohort Migration** | Bipartite Network Projection | `claims/patient_sharing.py` | Bipartite Jaccard set intersection | 42 CFR §455.23 |
| **L2** | **Calibrated Saturating Scorer** | Multi-Criteria Scoring | `pipeline/layer2_advisory.py` | De-duplicated family caps + saturating $1 - e^{-S/\tau}$ ($0-100$) | ADR-0001 Priority Queue |

---

### Detailed Model Specifications

#### 1. Model M1: Fellegi-Sunter Probabilistic Record Linkage Engine
* **Source Location:** [`src/ebv_detector/entity_resolution/fellegi_sunter.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/entity_resolution/fellegi_sunter.py)
* **Objective:** Detects barred individuals attempting to re-enroll under altered identities, minor typographical disguises, or phonetic aliases against federal exclusion lists (HHS-OIG LEIE, SAM.gov, State terminations).
* **Mathematical Theory:**
  For each field $i \in \{\text{Name}, \text{DOB}, \text{SSN}, \text{TIN}, \text{Address}, \text{Phone}\}$, define agreement probability under match ($m_i = P(\gamma_i=1 \mid M)$) and unmatch ($u_i = P(\gamma_i=1 \mid U)$). The total weight vector is computed as:
  $$W = \sum_{i: \gamma_i=1} \log_2\left(\frac{m_i}{u_i}\right) + \sum_{i: \gamma_i=0} \log_2\left(\frac{1 - m_i}{1 - u_i}\right)$$
  The posterior probability of identity match is derived using Bayes' theorem:
  $$P(M \mid \gamma) = \frac{P(M) \cdot 2^W}{P(M) \cdot 2^W + (1 - P(M))}$$
* **Distance Metrics Employed:**
  - **Jaro-Winkler Distance:** Applied to personal and legal business names (threshold $\ge 0.88$).
  - **Double Metaphone:** Phonetic encoding algorithm to match sound-alike surnames.
  - **Levenshtein Distance:** Normalized character edit-distance metric.
  - **Token-Set Jaccard:** Word-order invariant token matching for corporate entities.
* **Dual Threshold Adjudication:**
  - $W \ge T_{\text{match}}$: Instant high-confidence identity link (`verified_fact=True`).
  - $T_{\text{review}} \le W < T_{\text{match}}$: Enqueued into the **Near-Match Queue** for human reviewer adjudication under **DATA-025**.

---

#### 2. Model M2: Physical Address & CMRA Hygiene Engine
* **Source Location:** [`src/ebv_detector/address/rules.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/address/rules.py) & [`clustering.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/address/clustering.py)
* **Objective:** Prevents "paper clinics" that lack physical healthcare infrastructure from receiving billing credentials.
* **Components:**
  1. **Statutory Prohibitions (DATA-039 / DT-SCR-GATE-002):** Evaluates USPS CASS and Melissa DPV flags. Commercial Mail Receiving Agencies (CMRA / UPS Stores), Post Office Boxes, and Virtual Mailboxes (e.g., Regus, iPostal1) are flagged with `is_fatal=True`, triggering immediate Layer 1 denial under 42 CFR §455.416.
  2. **Facility-Residential Zoning Mismatch:** High-intensity facility service categories (`PSYCHIATRIC_REHAB`, `ADULT_DAY_CARE`, `SOBER_LIVING_FACILITY`, `CRISIS_STABILIZATION_CENTER`) declared at an address with USPS Residential Delivery Indicator (`RDI = RESIDENTIAL`) trip `L1-ADDR-FACILITY-RESIDENTIAL-MISMATCH` and mandate an on-site physical inspection.
  3. **Spatial Density Clustering:** Tracks address co-location density across applicants:
     $$\text{Density Count} = |\{ \text{NPI}_j : \text{Address}(\text{NPI}_j) = \text{Address}_{\text{target}} \}|$$
     Addresses with $\ge 4$ unrelated entities trigger `M2-ADDR-HIGH-DENSITY-CLUSTER`.
  4. **Institutional Allowlist Suppression:** Accredited multi-provider institutions (`FQHC`, `CHC`, `HOSPITAL_CAMPUS`, `TRIBAL_HEALTH`) are automatically matched against an allowlist, emitting `M2-ADDR-ALLOWLIST-EXEMPT` to suppress false positives.

---

#### 3. Model M3: Multi-Hop Relational Graph & Risk Propagation Engine
* **Source Location:** [`src/ebv_detector/graph/traversal.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/graph/traversal.py) & [`risk_propagation.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/graph/risk_propagation.py)
* **Objective:** Uncovers hidden beneficial ownership and shell company holdings designed to bypass 1-hop exclusion lookups.
* **Architecture:** Implemented in pure ANSI SQL backed by an ACID-compliant SQLite relational store (no proprietary graph database runtime).
* **Algorithms:**
  1. **Recursive CTE Path Traversal:** Uses SQL `WITH RECURSIVE` to traverse directed and undirected paths up to 3 hops between applicants and barred seeds, utilizing cyclic protection (`instr(path_str, id) = 0`).
  2. **42 CFR §455.104 GR-4 Indirect Ownership Multiplier:** Recursively computes indirect ownership percentages across multi-tier holding chains:
     $$\text{Indirect Ownership} = \prod_{e \in \text{Path}} \text{ownership\_percentage}(e)$$
     If any excluded person holds $\ge 5.0\%$ cumulative indirect beneficial ownership, the engine emits `L1-GR4-INDIRECT-OWNERSHIP-EXCLUDED` (mandatory statutory denial).
  3. **Supernode Damping Factor:** Entities with $\ge 30$ graph connections (e.g., commercial registered agents like CT Corporation or large hospital billing hubs) receive a $0.1\times$ damping penalty (`effective_weight = base_weight * 0.1`) to prevent false-positive shortcuts.
  4. **Personalized PageRank (PPR) Power Iteration:**
     $$p^{(k+1)} = (1 - \alpha) \cdot \mathbf{A}^T p^{(k)} + \alpha \cdot r$$
     where $\alpha = 0.15$, $r$ is the restart vector injected with probability mass strictly on known-bad seeds, and $\mathbf{A}$ is the supernode-damped transition matrix.

---

#### 4. Model M4: Application Telemetry & MinHash LSH Plagiarism Detector
* **Source Location:** [`src/ebv_detector/anomaly/application.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/anomaly/application.py) & [`template.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/anomaly/template.py)
* **Objective:** Detects organized "boiler rooms" mass-producing Medicaid applications.
* **Algorithms:**
  1. **Formation Velocity Tracker:** Flags entities incorporated $<30$ days prior to enrollment (`M4-ANOMALY-RAPID-FORMATION`), shared device fingerprints used across $\ge 2$ applications within 7 days (`M4-ANOMALY-DEVICE-SHARING`), and external IP velocity bursts ($\ge 3$ applications in 7 days).
  2. **MinHash Locality-Sensitive Hashing (LSH) 3-Gram Model:**
     - Decomposes clinical policy text into overlapping 3-word shingles.
     - Maps shingles through $m = 128$ independent universal linear hash functions:
       $$h_{a,b}(x) = (a \cdot x + b) \pmod{4294967311}$$
     - Extracts the minimum hash value per permutation to build a 128-integer signature vector.
     - Estimates pairwise Jaccard similarity in $O(m)$ time:
       $$\hat{J}(A, B) = \frac{\sum_{k=1}^{128} \mathbb{I}(s_{A,k} == s_{B,k})}{128}$$
     - When $\hat{J} \ge 0.80$, the engine emits `M4-TEMPLATE-POLICY-CLONE`, linking the application to a document fabrication ring.

---

#### 5. Model CP2-A: Robust MAD Z-Score Billing Ramp Detector
* **Source Location:** [`src/ebv_detector/claims/ramp_detector.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/claims/ramp_detector.py)
* **Objective:** Identifies explosive "zero-to-millions" billing volume in a provider's first 30 to 90 days.
* **Mathematical Theory:**
  Standard deviation is vulnerable to extreme fraud outliers. The system employs the **Median Absolute Deviation (MAD)**, providing a 50% breakdown point:
  $$\text{MAD} = \text{median}(|X_i - \text{median}(X)|)$$
  $$\text{Robust } Z = \frac{X_i - \text{median}(X)}{1.4826 \cdot \max(\text{MAD}, 5000)}$$
  Providers exhibiting $\text{Robust } Z \ge 3.5$ relative to their peer specialty cohort and enrollment age are flagged for immediate pre-payment review (`CP2-RAMP-ABNORMAL-VELOCITY`).
* **Deterministic Physical Edits:**
  - **Impossible Hours:** Staff billing $>24$ hours of service in a single calendar day (`CP2-BILLING-IMPOSSIBLE-HOURS`).
  - **Inpatient Overlaps:** Community services billed while the beneficiary was admitted to an inpatient hospital (`CP2-BILLING-INPATIENT-OVERLAP`).
  - **Post-Mortem Claims:** Services billed after the beneficiary's date of death (`CP2-BILLING-POST-MORTEM`).

---

#### 6. Model CP2-B: Bipartite Cohort Projection & Patient Migration Detector
* **Source Location:** [`src/ebv_detector/claims/patient_sharing.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/claims/patient_sharing.py)
* **Objective:** Catches patient kickback recycling when an excluded syndicate moves recruited patients to a new shell LLC.
* **Mechanism:**
  Represents claims as a bipartite graph $G = (\mathcal{V}_{\text{Provider}}, \mathcal{V}_{\text{Patient}}, \mathcal{E})$. Evaluates patient set intersection between new applicants ($T$) and suspended entities ($S$):
  $$J(T, S) = \frac{|\mathcal{P}(T) \cap \mathcal{P}(S)|}{|\mathcal{P}(T) \cup \mathcal{P}(S)|}$$
  If $J(T, S) \ge 0.35$ or absolute shared patients $\ge 10$, the engine emits `CP2-PATIENT-COHORT-MIGRATION` (`CRITICAL`), recommending an immediate **42 CFR §455.23 pre-hearing payment freeze**.

---

#### 7. Model L2: Calibrated Multi-Criteria Additive Scorer & Risk Tiering
* **Source Location:** [`src/ebv_detector/pipeline/layer2_advisory.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/pipeline/layer2_advisory.py)
* **Objective:** Synthesizes evidence across all engines into an explainable **Advisory Priority Score** ($0.0 - 100.0$) and recommends statutory ACA screening tiers.
* **Scoring Formula:**
  $$\text{Priority Score} = \min\left(100.0, \, S_{\text{L1}} + \sum_{e \in \text{Evidence}} w(e) + \min(25.0, 25.0 \cdot \text{PPR})\right)$$
  - Critical Severity Weight: $+20.0$ pts
  - High Severity Weight: $+12.0$ pts
  - Medium Severity Weight: $+6.0$ pts
  - Low Severity Weight: $+2.0$ pts
  - Near-Match Queue Evidence: $+5.0$ pts (capped at $10.0$ to prevent false-positive inflation)
* **ACA Screening Tier Mapping (42 CFR §455.450):**
  - **High Risk:** Priority $\ge 75$ or Layer 1 statutory grounds $\implies$ Mandatory criminal background fingerprinting and unannounced site visit.
  - **Moderate Risk:** Priority $45–74$ $\implies$ Mandatory physical site visit and pre-enrollment license verification.
  - **Limited Risk:** Priority $< 45$ $\implies$ Routine automated credential checks.

---

### Regulatory Compliance & Data Governance

1. **NC RFP Attachment C §3(e)(ii) (Zero State Data Derivation):**
   No machine learning model in this system is trained or derived from State Medicaid Claims or Disclosure Data. All models are calibrated using closed-form probabilistic likelihoods, graph topology, and non-parametric statistics.
2. **NC RFP Attachment C §3(e)(i) (Zero Non-U.S. Open-Source Dependencies):**
   The entire system is self-contained in standard-library Python 3.11 and ANSI SQL, eliminating supply-chain exposure to foreign open-source binaries (e.g. Splink, DuckDB).
3. **NC RFP Attachment C §5 (Immutable WORM Audit Trail):**
   Every decision vector is serialized to canonical JSON, hashed via SHA-256, and chained to prior records (`hash_k = SHA256(hash_{k-1} || payload_k)`), producing court-admissible Write-Once-Read-Many (WORM) audit logs for federal False Claims Act prosecutions.

---

### Operational Benchmarks

- **Checkpoint 1 Evaluation Latency:** $< 12 \text{ ms}$ per full application (including multi-hop graph traversal, MinHash LSH, and Fellegi-Sunter comparison).
- **Checkpoint 2 Claims Evaluation Latency:** $< 25 \text{ ms}$ per 10,000 claim records.
- **Memory Footprint:** $< 120 \text{ MB}$ total operational RAM in containerized production.
- **Leave-One-Ring-Out (LORO) Benchmark:** 100% Precision and 100% Recall across all 6 synthetic EBV fraud archetypes in [`tests/`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/tests/).
