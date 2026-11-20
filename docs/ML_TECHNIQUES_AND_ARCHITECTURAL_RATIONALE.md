# Medicaid Provider Fraud Intelligence System (EBV Detector)
## Machine Learning Techniques & Architectural Decisions Rationale
**Document ID:** `DOC-EBV-ML-RATIONALE-001`  
**Classification:** State Medicaid Program Integrity / Engineering & Executive Reference  
**Target Threat:** "Enroll, Bill, Vanish" (EBV) Sham Healthcare Provider Syndicates  
**Compliance Standards:** 42 CFR Part 455, NC RFP Attachment C §3, ADR-0001 Architecture  
**Release Version:** `2.0.0-PROD`

---

## Executive Overview

This document provides a rigorous technical breakdown of **every machine learning and statistical model used in the EBV Fraud Detection System**, specifying:
1. **How** each technique is implemented.
2. **Why** that specific mathematical method was chosen over alternative approaches.
3. The **actual real-world legal, statistical, operational, and fraud-mechanic reasons** behind every architectural decision.

```
                                  INCOMING DATA STREAM
                                           │
         ┌─────────────────────────────────┴─────────────────────────────────┐
         ▼                                                                   ▼
[ Checkpoint 1: Pre-Enrollment ]                             [ Checkpoint 2: Early Billing (Days 1–90) ]
  • Fellegi-Sunter Bayesian Linkage (M1)                       • Non-Parametric Robust MAD Z-Score (CP2-A)
  • Allowlist-Constrained Density Clustering (M2)              • CUSUM Exponential Velocity Alarm (CP2-A)
  • ANSI SQL Recursive CTE Multi-Hop Graph (M3-A)              • Bipartite Jaccard Patient Migration (CP2-B)
  • GR-4 Indirect Ownership Path Multiplication                • Hard Physical Edits (>24h Clinician, Inpatient)
  • Supernode Degree Attenuation (M3)
  • Personalized PageRank Random Walk (M3-B)
  • 128-Permutation MinHash LSH Text NLP (M4-B)
  • Sliding-Window Telemetry Velocity (M4-A)
  • Saturating Exponential Scoring Transform (L2)
```

---

# Part 1: All ML & Statistical Techniques Explained

---

### 1. Fellegi-Sunter Probabilistic Record Linkage Engine (M1)
* **Code Location:** [`src/ebv_detector/entity_resolution/fellegi_sunter.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/entity_resolution/fellegi_sunter.py)
* **Mathematical Category:** Bayesian Generative Record Linkage
* **Formulation:**
  For each field $i \in \{\text{NPI}, \text{TIN}, \text{SSN4+DOB}, \text{Phone}, \text{Address}, \text{Legal Name}, \text{EFT}, \text{Device}\}$:
  $$w_{\text{agree}} = \log_2\left(\frac{m_i}{u_i}\right), \quad w_{\text{disagree}} = \log_2\left(\frac{1 - m_i}{1 - u_i}\right)$$
  $$\text{Total Log-Odds Weight } W = \sum_{i} w_i$$
  $$\text{Posterior Probability } P(M \mid \gamma) = \frac{P(M) \cdot 2^W}{P(M) \cdot 2^W + (1 - P(M))}$$
* **String Comparators Used:**
  - Jaro-Winkler (character transpositions and prefix bonus).
  - Levenshtein Distance (minimum edit operations).
  - Double Metaphone (phonetic encoding for Anglo and Slavic/European names).
  - Token-Set Similarity (handles word re-ordering, e.g., *"Apex Care Solutions"* vs *"Care Solutions Apex"*).
* **How It Is Used:**
  - Partitioning candidates using 6 multi-pass blocking keys to avoid $O(N^2)$ candidate pairs.
  - Computing agreement weights across all fields.
  - Prior match probability $P(M) = 0.001$.
  - Thresholds: Auto-Link $\ge 12.0$ bits, Near-Match Queue $\in [5.0, 12.0)$ bits, Non-Match $< 5.0$ bits.
* **Why It Was Chosen Over Alternatives:**
  - *Why not deterministic exact matching?* Fraudsters intentionally introduce typos, change corporate suffixes (LLC $\to$ Inc), or alter phonetic spellings to defeat SQL `WHERE` queries.
  - *Why not deep learning / BERT embeddings?* Embeddings are non-transparent black boxes that cannot be defended in state administrative hearings. Fellegi-Sunter allows examiners to explain the exact bit contribution of each field in court.

---

### 2. Allowlist-Constrained Density Clustering (M2)
* **Code Location:** [`src/ebv_detector/address/clustering.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/address/clustering.py)
* **Mathematical Category:** Spatial / Address Co-Location Clustering
* **Formulation:**
  $$\text{Cluster Weight} = \min\left(2.0, \log_2\left(\frac{\max(1, N_{\text{entities}})}{3}\right)\right)$$
* **How It Is Used:**
  - Groups entities by normalized address strings (`STREET, CITY, STATE, ZIP5`).
  - Penalizes addresses hosting multiple unrelated provider organizations.
  - Cross-references the **Categorical Facility Allowlist**: Federally Qualified Health Centers (FQHC), Community Health Centers (CHC), Hospital Campuses, and Tribal Health facilities are exempted from density penalties.
* **Why It Was Chosen Over Alternatives:**
  - *Why not DBSCAN or K-Means?* Physical Medicaid fraud does not happen in continuous Euclidean space; it happens at discrete postal delivery points. DBSCAN would group neighboring legitimate buildings together. Exact suite clustering with an FQHC allowlist isolates shell suites without creating false positives on legitimate hospital complexes.

---

### 3. ANSI SQL Recursive CTE Multi-Hop Graph Traversal (M3-A)
* **Code Location:** [`src/ebv_detector/graph/traversal.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/graph/traversal.py)
* **Mathematical Category:** Relational Graph Traversal via SQL Recursion
* **Formulation:**
  Breadth-first search executed natively inside SQLite using `WITH RECURSIVE`:
  ```sql
  WITH RECURSIVE path_cte AS (
      SELECT source_id, target_id, edge_type, 1 AS hops, ...
      UNION ALL
      SELECT e.source_id, e.target_id, e.edge_type, p.hops + 1, ...
  )
  ```
* **How It Is Used:**
  - Traverses 2 to 3 hops between applicant NPIs, bank accounts, phones, and known-bad seeds.
  - Filters paths by temporal date bounds (`effective_start <= app_date` and `effective_end >= app_date`).
* **Why It Was Chosen Over Alternatives:**
  - *Why not Neo4j or external graph databases?* Neo4j introduces external JVM infrastructure, network latency, and procurement overhead. Executing recursive CTEs directly inside the existing relational database provides sub-5-millisecond traversal latencies with zero infrastructure dependencies.

---

### 4. GR-4 Indirect Ownership Path-Product Multiplication
* **Code Location:** [`src/ebv_detector/graph/traversal.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/graph/traversal.py) (`calculate_gr4_indirect_ownership`)
* **Mathematical Category:** Multiplicative Path Weight Accumulation along DAGs
* **Formulation:**
  $$\text{Effective Indirect Equity} = \prod_{e \in \text{path}} \text{ownership\_percentage}(e)$$
* **How It Is Used:**
  - Multiplies equity percentages across corporate holding chains (e.g., Excluded Owner $60\% \times$ Holding Company $80\% = 48.0\%$).
  - Checks if result $\ge 5.0\%$ statutory threshold under 42 CFR §455.104.
* **Why It Was Chosen Over Alternatives:**
  - Fraud rings intentionally stack multiple corporate tiers to dilute direct ownership below 5% at each level. Only recursive path multiplication accurately computes true beneficial equity.

---

### 5. Supernode Degree Attenuation
* **Code Location:** [`src/ebv_detector/graph/supernodes.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/graph/supernodes.py)
* **Mathematical Category:** Graph Degree Regularization
* **Formulation:**
  $$\text{Edge Weight}(u, v) = \begin{cases} 0.1 \times \text{Base Weight}, & \text{if } \text{degree}(u) > 30 \text{ or } \text{degree}(v) > 30 \\ \text{Base Weight}, & \text{otherwise} \end{cases}$$
* **How It Is Used:**
  - Identifies hubs with $>30$ connections (e.g., commercial registered agents like CT Corporation).
  - Reduces transition weights through these nodes by 90%.
* **Why It Was Chosen Over Alternatives:**
  - Commercial registered agents incorporate thousands of legitimate businesses. Without degree attenuation, if one rogue actor used that agent, thousands of clean, honest doctors would be falsely flagged as 2-hop associates of a fraud ring (guilt by association).

---

### 6. Personalized PageRank (PPR) / Random Walk with Restart (M3-B)
* **Code Location:** [`src/ebv_detector/graph/risk_propagation.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/graph/risk_propagation.py)
* **Mathematical Category:** Spectral Graph Centrality / Random Walk
* **Formulation:**
  $$p^{(k+1)} = (1 - \alpha) \mathbf{A}^T p^{(k)} + \alpha r$$
  - $\alpha = 0.15$ (restart probability).
  - $r$: Normalized restart vector with mass placed on active sanction seeds.
* **How It Is Used:**
  - Propagates risk scores from adverse seeds across the relationship graph.
  - **Suppression Rule (§3.2, §3.6):** PageRank points are added **only when no direct graph path or seed match explains the affiliation**, preventing double-counting.
* **Why It Was Chosen Over Alternatives:**
  - *Why not standard PageRank?* Standard PageRank measures general importance across the entire web. Personalized PageRank teleports back specifically to adverse seeds, measuring topological proximity to fraud rings.

---

### 7. 128-Permutation MinHash Locality-Sensitive Hashing (LSH) (M4-B)
* **Code Location:** [`src/ebv_detector/anomaly/template.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/anomaly/template.py)
* **Mathematical Category:** Locality-Sensitive Hashing / Dimensionality Reduction
* **Formulation:**
  $$J(A, B) \approx \frac{1}{128} \sum_{k=1}^{128} \mathbb{I}\left(h_k(A) = h_k(B)\right), \quad h_k(x) = (a_k x + b_k) \pmod p$$
* **How It Is Used:**
  - Deconstructs policy manuals into overlapping 3-character shingles.
  - Projects documents into a 128-integer fingerprint.
  - Compares fingerprints across prior applications. Jaccard similarity $\ge 0.80$ triggers `M4-TEMPLATE-POLICY-CLONE`, scored logarithmically: $1.5 + \log_2(1 + \text{matches})$.
* **Why It Was Chosen Over Alternatives:**
  - Comparing full text across thousands of 100-page manuals is computationally intractable ($O(N^2 \cdot L)$); MinHash compresses 100-page documents into 128 numbers and computes similarity in microseconds with provable probabilistic error bounds ($\epsilon \le 1/\sqrt{128} \approx 8.8\%$).

---

### 8. Sliding-Window Telemetry Velocity Tracking (M4-A)
* **Code Location:** [`src/ebv_detector/anomaly/application.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/anomaly/application.py)
* **Mathematical Category:** Sliding Temporal Window Event Frequency Analysis
* **Formulation:**
  $$\text{Count}(e, t_{\text{app}}, \Delta t) = \sum_{a \in \text{History}} \mathbb{I}\left(\text{key}(a) = e \land 0 \le (t_{\text{app}} - t_a) \le \Delta t\right)$$
  - Device & IP window: $\Delta t = 7$ days.
  - Email reuse window: $\Delta t = 30$ days.
* **How It Is Used:**
  - Flags $>3$ submissions from the same device/IP within 7 days, or $>3$ from the same email within 30 days.
* **Why It Was Chosen Over Alternatives:**
  - Without sliding windows, three applications submitted over three years would be penalized the same as three submitted in one afternoon. Time-windowing specifically catches coordinated burst filings.

---

### 9. Robust Median Absolute Deviation (MAD) Outlier Model (CP2-A)
* **Code Location:** [`src/ebv_detector/claims/ramp_detector.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/claims/ramp_detector.py)
* **Mathematical Category:** Non-Parametric Robust Dispersion Estimation
* **Formulation:**
  $$\text{MAD} = \text{median}\left(|X_i - \text{median}(X)|\right)$$
  $$\text{Robust } Z = \frac{X - \text{median}(X)}{1.4826 \times \text{MAD}}$$
* **How It Is Used:**
  - Compares early billing against an active peer cohort of the exact same service category.
  - If robust $Z \ge 3.5$, flags a billing velocity anomaly; if $Z \ge 5.0$, triggers an exponential CUSUM ramp alarm.
* **Why It Was Chosen Over Alternatives:**
  - Standard Gaussian metrics (mean and standard deviation) break down in fraud analytics because extreme fraud values (e.g. billing \$5,000,000 in month 2) skew the mean and massively inflate the standard deviation, making other fraudulent spikes look "normal". Median and MAD are mathematically robust against up to 50% contamination in the data.

---

### 10. Bipartite Jaccard Patient Cohort Migration Detector (CP2-B)
* **Code Location:** [`src/ebv_detector/claims/patient_sharing.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/claims/patient_sharing.py)
* **Mathematical Category:** Bipartite Network Projection & Jaccard Overlap
* **Formulation:**
  $$J(P_{\text{new}}, P_{\text{suspended}}) = \frac{|P_{\text{new}} \cap P_{\text{suspended}}|}{|P_{\text{new}} \cup P_{\text{suspended}}|}$$
* **How It Is Used:**
  - When law enforcement shuts down a provider, the system indexes their patient roster.
  - When a newly enrolled provider bills claims, it computes the intersection. If $\ge 15$ patients or $\ge 50\%$ of the new provider's roster were recycled from a suspended provider (and the source was sanctioned *before* the new provider began billing), it raises an alert.
* **Why It Was Chosen Over Alternatives:**
  - Corrupt patient recruiters steer recruited patient blocks from shut-down clinics to newly opened shell providers. Bipartite patient tracking detects patient recycling the moment the new entity starts billing.

---

### 11. Saturating Exponential Scoring Transform (Layer 2)
* **Code Location:** [`src/ebv_detector/pipeline/layer2_advisory.py`](file:///Users/saivinayrayala/Documents/ML%20Engineer%20Handoff%20Enroll%20Bill%20Vanish/src/ebv_detector/pipeline/layer2_advisory.py)
* **Mathematical Category:** Bounded Exponential Saturation Function
* **Formulation:**
  $$S = \sum_{\text{families}} \text{capped\_family\_score}, \quad \text{Priority Score} = 100 \times \left(1 - \exp\left(-\frac{S}{4.0}\right)\right)$$
  $$\text{Contribution}_{\text{family}} = \text{Priority Score} \times \left(\frac{\text{capped\_family\_score}}{S}\right) \implies \sum \text{contributions} \equiv \text{Priority Score}$$
* **How It Is Used:**
  - Groups evidence into 6 non-overlapping families with diminishing returns ($1.0, 0.5, 0.25$) and family caps.
  - Caps: `AFFILIATION`: 6.0, `OWNERSHIP`: 5.0, `ADDRESS`: 4.0, `SUBMISSION`: 4.0, `IDENTITY`: 4.0, `AGE`: 1.5.
  - Normalizes feature contributions so they sum **identically** to the displayed priority score.
* **Why It Was Chosen Over Alternatives:**
  - Unbounded linear addition creates severe score distortion (a provider with multiple correlated red flags on one issue could score 150+). Exponential saturation reflects real-world risk: once a clinic has 2-3 severe anomalies, it is already high risk; adding 10 more copies of the same issue shouldn't inflate the score past 100. Furthermore, normalizing contributions guarantees that displayed feature contributions sum **identically** to the displayed score, providing complete transparency.

---

# Part 2: Actual Real-World Reasons Behind Every Architectural Decision

---

### Decision 1: Strict Dual-Layer Architecture (Layer 1 vs Layer 2)
* **The Decision:** Layer 1 is deterministic and authoritative; Layer 2 is assistive machine learning that **never auto-denies**.
* **The Actual Reason:** **Constitutional Due Process (14th Amendment) & 42 CFR Part 455.**
  A state Medicaid agency cannot legally revoke a provider’s license or deny enrollment based on an algorithm or black-box score without exposing the state to massive civil rights litigation. Denials must be rooted in documented statutory violations (e.g. OIG exclusion, prohibited address) determined by a human hearing officer. Layer 1 establishes the legal basis for review; Layer 2 simply orders the reviewer's queue.

---

### Decision 2: Removing Layer 1 Statutory Points & ACA Tiers from the Queue Score
* **The Decision:** Statutory grounds ($+50$) and ACA categorical tiers ($+25/+10$) are **completely excluded** from the $0-100$ priority score.
* **The Actual Reason:** **Preventing Queue Distortion and Screening Paralysis.**
  Under 42 CFR §455.450, categories like Home Health, Hospice, and DME are legally mandated to be "High Risk", while Behavioral Health is "Moderate Risk". If ACA tier points were added to the score, clean family doctors started at 0 while clean home health agencies started at 25–35, drowning reviewers in false alarms on legitimate providers. Furthermore, adding $+50$ for statutory grounds caused all excluded providers to tie at 100, leaving screeners unable to distinguish between an administrative license lapse and an active criminal ring. The priority score now measures anomaly severity alone.

---

### Decision 3: Canonical Fact Key De-Duplication
* **The Decision:** Every evidence item carries a `fact_key` (e.g. `EFT:053000196_9876543210`), and items with the same key collapse to the single strongest detector.
* **The Actual Reason:** **Eliminating Multicollinearity and False Alarm Explosion.**
  In the earlier version of the system, a single compromised bank account added points across 4 separate detectors: Layer 1 EFT match ($+50$), Layer 2 severity ($+20$), Relational Graph path ($+20$), and PageRank ($+25$), tallying over 122 points from one bank account! A reviewer looking at the screen would assume there were four separate fraud vectors when there was really only one. Collapsing by `fact_key` ensures that **one underlying fact is scored exactly once**.

---

### Decision 4: Grouping Evidence into 6 Families with Caps
* **The Decision:** Evidence is partitioned into `AFFILIATION`, `OWNERSHIP`, `ADDRESS`, `SUBMISSION`, `IDENTITY`, and `AGE`, each with an expert cap and geometric decay ($1.0, 0.5, 0.25$).
* **The Actual Reason:** **Preventing Single-Detector Hijacking of the Review Queue.**
  A commercial mail drop might be flagged as a CMRA, a virtual office, a PMB, and a high-density cluster. If these were added linearly, a simple mailbox issue would monopolize the top of the investigative queue, pushing multi-million-dollar criminal syndicates down. By capping Address at 4.0 points, an application must exhibit red flags across **multiple independent dimensions** (e.g., bad address + hidden owner + rapid formation) to achieve a Critical priority score ($>70$).

---

### Decision 5: Hard Identifier Conflict Demotion in Fellegi-Sunter
* **The Decision:** If an applicant shares a business name and phone with an existing entity but has **different NPI or TIN**, the engine deducts negative weights and **demotes the match from `AUTO_LINK` to `SEND_TO_QUEUE`**.
* **The Actual Reason:** **Preventing Identity Misattribution and False Mergers.**
  In corporate databases, legitimate businesses often share similar names (e.g., *"Carolina Health Services"*) or inherit re-assigned commercial phone numbers. If an algorithm auto-linked them despite different NPIs and TINs, an innocent clinic could have its Medicaid payments frozen because a completely unrelated entity with a similar name was sanctioned. Demoting to a human queue ensures human screeners verify corporate re-organizations before merging records.

---

### Decision 6: Zero Training or Fine-Tuning on State Data (NC RFP Attachment C §3)
* **The Decision:** All weights, likelihood ratios, and thresholds are expert-calibrated and bounded; no models are trained on State Data.
* **The Actual Reason:** **Model Drift, Demographic Bias, and Legal Procurement Compliance.**
  State enrollment and claims data represent "positive-unlabeled" data: just because a provider wasn't investigated doesn't mean they are legitimate. Training an unsupervised Isolation Forest or supervised classifier on state data bakes historical investigative biases into the algorithm, causing it to discriminate against small rural practices or non-traditional clinics. Expert-calibrated, bounded rules are 100% auditable, legally defensible, and contract-compliant.

---

### Decision 7: Append-Only WORM Audit Ledger for Reviewer Overrides
* **The Decision:** Reviewer overrides never mutate prior audit records; they append a new `REVIEWER_DECISION` record referencing `parent_record_id` and updating the SHA-256 chain.
* **The Actual Reason:** **Evidentiary Non-Repudiation in Federal Criminal Prosecutions.**
  When Medicaid fraud cases go to federal court (DOJ/OIG criminal trials), defense attorneys routinely subpoena the state’s software records to allege that the state tampered with evidence or retroactively changed algorithmic scores to target their client. A cryptographically chained ledger provides mathematical proof that records were created at the exact stated timestamp and have never been altered.

---

### Decision 8: Checkpoint 2 Focuses on the First 90 Days
* **The Decision:** Claims surveillance analyzes rolling 30-to-90-day windows, flagging rapid ramps and patient migrations immediately.
* **The Actual Reason:** **The "Vanish" Mechanics of EBV Fraud.**
  EBV syndicates have no intention of providing long-term care. Their business model relies on submitting \$3M–\$5M in claims during the first 3 months and abandoning the entity before annual revalidation or post-payment audits begin. Traditional claims analytics (which require 12–24 months of claims history) always detect EBV fraud **too late**—after the money is already gone. Checkpoint 2 is placed in the first 90 days specifically to trigger **42 CFR §455.23 payment suspensions** while funds are still recoverable in state accounts.

---

### Decision 9: PageRank Suppression When Direct Path Explains Affiliation
* **The Decision:** If an application already has a direct 1-to-3-hop graph path to an adverse seed, its Personalized PageRank score is suppressed to zero in the scoring engine.
* **The Actual Reason:** **PageRank is a Proxy for Unseen Proximity, Not a Double Count.**
  Personalized PageRank exists to detect diffuse network clustering when no explicit link can be identified. If an applicant already directly connects to an excluded provider via a shared bank account, the relationship is a deterministic fact. Adding PageRank on top of that path is mathematically double-counting the exact same walk through the graph.

---

### Decision 10: Idempotent Graph Store with Unique Indices
* **The Decision:** The SQLite graph store uses a unique composite index `(source_id, target_id, edge_type, effective_start)` and `ON CONFLICT DO UPDATE` upserts.
* **The Actual Reason:** **Preventing Graph Topology Distortion from Repeated Scoring.**
  In real-world state portals, providers resubmit applications, update demographic fields, or run "what-if" pre-checks. Plain `INSERT` statements created duplicate edges every time an application was scored. Over time, an NPI with 10 re-submissions accumulated 40 identical edges, which artificially inflated PageRank and degree density scores. Upserting guarantees topological stability regardless of scoring frequency.

---

# Part 3: Review & Defense Cheat Sheet

| Question | Direct Answer |
|---|---|
| **Can the AI auto-deny an applicant?** | **No.** Under ADR-0001 and 42 CFR Part 455, Layer 1 flags statutory grounds (`GROUNDS_PENDING_HUMAN_DECISION`), and Layer 2 produces an assistive priority score ($0-100$). Only human hearing officers have legal adverse authority. |
| **How do you avoid double-counting correlated red flags?** | Every detector assigns a canonical `fact_key`. Multiple detectors observing the same entity collapse into the single highest weight. |
| **How do you prevent score inflation on massive rings?** | The saturating transform $\text{Priority} = 100 \times (1 - \exp(-S/4))$ guarantees scores remain smoothly bounded between $0$ and $100$, and feature contributions sum identically to the score. |
| **Why not train an XGBoost or Random Forest model on state claims?** | Violates NC RFP Attachment C §3(e)(ii) (prohibiting derivation from state data) and introduces severe selection bias from positive-unlabeled historical audits. |
| **How does the system handle corporate holding companies hiding masterminds?** | Multi-tier equity multiplication along ANSI SQL recursive graph edges computes total effective indirect equity under 42 CFR §455.104 ($\ge 5.0\%$). |
| **What happens if a human reviewer overrides an algorithm's decision?** | The system appends a new `REVIEWER_DECISION` record referencing `parent_record_id` and updating the SHA-256 cryptographic chain. Prior records are never modified. |
| **How do you catch patient recruiters transferring patient blocks?** | Bipartite Jaccard network projection detects when $\ge 15$ patients or $\ge 50\%$ of a new provider's roster were recycled from a suspended provider. |
