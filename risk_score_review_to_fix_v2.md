# Risk Score Review — Items to Fix (v2, merged)

| Document Metadata | Specification |
|---|---|
| **Date** | 2026-11-18 |
| **Supersedes** | `risk_score_review_to_fix.md` (v1). v1 is kept unchanged for history. |
| **Scope** | Layer 1 / Layer 2 scoring, the signals that feed it, the scoring API, the audit chain, Checkpoint 2 actions, the benchmark, and the governance needed before any ML |
| **Status** | **Open — to be fixed** |
| **Governance** | ADR-0001: the score stays assistive. It must not deny, approve, or set the ACA risk tier. NC RFP Attachment C §3(e)(ii): no training, fine-tuning, or improving ML on State Data. |

---

## 0. What changed from v1

**Corrections to v1 (checked by running the code):**

| v1 claim | What the code does | Section |
|---|---|---|
| "A shared device alone can clear the auto-link threshold." | It does not. `legal_name` is always compared. Device-only plus a dissimilar name scores **8.49 → near-match queue**. The real defect is that **NPI and TIN are never compared**. Two applications with the same name and phone but *different* NPI and TIN score **18.63 → AUTO_LINK**. | 3.7 |
| "Damping on a hub cancels." | Only partly. For walks *leaving* a hub whose edges are all damped, the 0.1 divides out. Walks *into* the hub from undamped neighbors are still reduced. | 3.6 |
| "Missing fields contribute 0 rather than uncertainty." | A neutral weight of 0 for a missing field is standard Fellegi–Sunter practice. The gap is that an auto-link can rest on one or two weak fields, and the explanation does not say how many fields were compared. | 3.7 |

**New items found in this review:**

| # | Item |
|---|---|
| 3.10 | Scoring changes state. Each call appends duplicate graph edges. API IDs can collide. There is no separate score path and ingest path. |
| 3.11 | The audit chain is not WORM. A reviewer override is written into the stored record in place and the chain hash does not change. |
| 3.12 | Seed-list hygiene: duplicate seeds create duplicate `LINKED_SEED` edges, and a provider NPI can overwrite a seed node. |
| 3.13 | `verified_fact=True` is set on statistical patterns (velocity, template similarity, density, billing ramp), not only on record matches. |
| 3.14 | PageRank is recomputed over the whole graph on every request. The 0.05 alert threshold is about 1.8e-5 in raw PageRank. |
| 3.15 | Benchmark independence: the scenarios were written to trigger the detectors, so 100% recall is circular. |
| 3.16 | Calibration, review capacity, drift, and fairness monitoring are missing. |
| 3.17 | Model governance is missing (versioned weights, State review, shadow mode). It is needed before any ML. |

---

## 1. Verdict

The 0–100 `priority_score` is a tally of evidence points. It is a weak ranker outside the synthetic demo, for three reasons:

1. **It double-counts.** One fact is scored up to five times (worked example in §2.1).
2. **It mixes decisions with ranking.** Statutory grounds and ACA tier are added into the same number used to order the queue.
3. **It is not calibrated.** "CRITICAL" does not correspond to a measured hit rate, and there is no link to reviewer capacity.

The published 100% precision and recall do not measure ranking. The harness marks an application as flagged when any of these is true:

- Layer 1 status is `REVIEW_REQUIRED` or `DENIED_GROUNDS`;
- the level is `HIGH` or `CRITICAL`;
- Checkpoint 2 sets a site-visit or payment-suspension flag.

Every Moderate or High ACA category is already `REVIEW_REQUIRED`, so the benchmark passes before the numeric score orders anything. The scenarios were also authored alongside the detectors, so each one is guaranteed to fire (§3.15).

---

## 2. How the score works today

`Layer2AdvisoryEngine.compute_advisory_package` in `src/ebv_detector/pipeline/layer2_advisory.py` adds three terms and caps the total at 100:

| Term | Points | Where |
|---|---|---|
| Layer 1 already found statutory grounds (`DENIED_GROUNDS`) | +50 | `layer2_advisory.py` |
| Else ACA tier High / Moderate | +25 / +10 | same |
| Each evidence item by severity: Critical / High / Medium / Low | +20 / +12 / +6 / +2 | same |
| Near-match items only | +5 each, cap 10 | same |
| Personalized PageRank proximity | `min(25, scaled_ppr × 25)` | `graph/risk_propagation.py` `get_risk_score` |

`composite_risk_level` does not follow that number. It is `CRITICAL` when any of these holds:

- the score is at least 75;
- any statutory ground exists;
- any single Critical evidence item exists.

`CRITICAL` triggers a Program Integrity referral and a prepayment hold. `feature_contributions` are recorded before the cap, so they can add up to more than the score that is displayed.

### 2.1 Worked example (measured): Scenario 3, BEACON SOUTHERN HEALTHCARE LLC

Setup: a fresh pipeline with the synthetic seeds and ownership edges, then the first 10 synthetic applications evaluated (the same warm-up `api/server.py` does at start). BEACON is a Behavioral Health LLC formed 26 days before applying. Its EFT `053000196_9876543210` matches a provider terminated for cause.

Graph after sync:

```
1900000050 --USES_EFT--> EFT-053000196_9876543210 --LINKED_SEED--> SEED-EFT-053000196_9876543210   (edge present twice)
1900000050 --USES_ADDRESS / USES_PHONE / SUBMITTED_FROM--> address, phone, device nodes
PERSON-6677_1980-02-20 --DIRECT_OWNERSHIP--> 1900000050
```

Path to seed: 2 hops. Raw PPR 0.1056, scaled 0.8048.

Layer 1: tier `MODERATE`, status `DENIED_GROUNDS`, ground "42 CFR 455.416(a) — Bank account associated with previously terminated provider".

Layer 2 contributions:

| Contribution | Points | Underlying fact |
|---|---:|---|
| `layer1_statutory_grounds` | 50.0 | shared EFT |
| `L1-SEED-EFT-SHARED-TERMINATION` (Critical) | 20.0 | shared EFT |
| `M3-GRAPH-PATH-TO-SANCTIONED-SEED` (Critical) | 20.0 | shared EFT |
| `M3-PPR-SEED-PROXIMITY-ALERT` (High) | 12.0 | shared EFT |
| `m3_seed_proximity_ppr` | 20.1 | shared EFT |
| `M4-ANOMALY-RAPID-FORMATION` (Medium) | 6.0 | entity age |
| **Sum of contributions** | **128.1** | |
| **Displayed** | **100 / CRITICAL** | |

122.1 of the 128.1 points come from **one bank account**. A copied policy manual adds +12 once per earlier matching application (`M4-TEMPLATE-POLICY-CLONE`), so ten copies exceed 100 points from templates alone.

---

## 3. Fixes

### 3.1 Take Layer 1 out of the queue score

**Code:** `pipeline/layer2_advisory.py` (base points); `pipeline/layer1_rules.py` (`evaluate_application` status, about lines 211–220; `evaluate_address_rules` call at line 182); `models/results.py` line 20.

**What happens now:**

- Statutory hits and the ACA category are added into the same 0–100 number used to order the review queue, so denied cases tie at the top.
- A clean high-tier provider (home health, HCBS, DME, hospice, NEMT, personal care) starts at 25. A clean behavioral-health or ABA provider starts at 10.
- Layer 1 emits `DENIED_GROUNDS` as soon as a statutory ground exists, but the enum comment in `models/results.py` says "Human confirmed 42 CFR 455.416 grounds".
- `evaluate_address_rules(app.primary_address, app.service_category)` is called without `config`, so config overrides for address rules are ignored.

**Fix:**

- [ ] Keep `Layer1Decision` as the statutory record: grounds, ACA tier, and required screening actions (42 CFR 455.432 / 455.434).
- [ ] Stop adding `layer1_statutory_grounds` (+50), Layer 1 verified-fact points, and `aca_tier_high` / `aca_tier_moderate` into `priority_score`.
- [ ] Rename the automatic status to something like `GROUNDS_PENDING_HUMAN_DECISION`. Only a reviewer action creates a denial.
- [ ] Route applications with statutory grounds to their own queue. Use `priority_score` to rank within each queue.
- [ ] Show the ACA tier beside the score. Do not add it in.
- [ ] Derive `composite_risk_level` from score bands set by review capacity (§3.16), not from any single evidence item.
- [ ] Make recommended actions follow the evidence type (site visit for address, bank-ownership proof for a shared EFT). Do not recommend a prepayment hold from the enrollment score alone.
- [ ] Pass `config` into `evaluate_address_rules`.

### 3.2 Score each underlying fact once, with a cap per family

**Code:** `pipeline/layer2_advisory.py` severity weights; evidence emitted from `pipeline/checkpoint1.py`, `anomaly/application.py`, `anomaly/template.py`, `address/rules.py`.

**What happens now:**

- Every evidence item adds its full severity weight.
- Correlated detectors for one identifier all add (§2.1).
- Template and high-confidence entity-resolution links add once per prior application, with no cap except for near-matches.
- A shared device fingerprint is `CRITICAL` by itself (`anomaly/application.py`), even when the device belongs to a credentialing vendor.

**Fix:**

- [ ] Give every evidence item a `fact_key`, for example `EFT:053000196_9876543210`. Items with the same key collapse to the strongest one.
- [ ] Group facts into non-overlapping families: affiliation, ownership, address, submission behavior, identity, entity age.
- [ ] Within a family, use diminishing returns: full weight for the strongest hit, then ½, then ¼, up to the family cap.
- [ ] Combine families with a saturating transform, so one strong family scores high and several families approach 100:
  `S = Σ capped family scores`, `priority = 100 × (1 − exp(−S / k))`, with `k` in config (start at 4).
- [ ] Scale each family's contribution by `priority / S`, so the contributions add up exactly to the displayed score.
- [ ] Score template similarity from the strongest match plus `log2(1 + unrelated matches)`, not +12 per pair. Down-weight a template shared by many unrelated clean entities.
- [ ] Count a shared device, IP, or email only when the other applications have a different TIN and no common owner. Same-organization reuse goes to the near-match queue (PDM-REQ-DATA-025 / PDM-REQ-SEC-052).
- [ ] Keep weights in `config.py`, version them, and hash the weight version into the audit record (§3.11).

Suggested starting caps, in relative units. These are expert-set, not trained on State Data. Tune them against the ranking benchmark (§3.15) and have the State review them (§3.17).

| Family | Cap | Starting weights |
|---|---|---|
| Affiliation | 6 | Active adverse seed by EFT or SSN 5; phone 3; device 2.5; same-suite address 2; shared clinician 1.5. Halve at 3 hops. PageRank only when no path explains it. |
| Ownership | 5 | Excluded upstream owner below 5% on dated edges 2.5; disclosed parent with no ownership edge 2. |
| Address | 4 | One collapsed mailbox item 3; vacant 2; unrelated entities in the same building `log2(n/3)`, max 3. |
| Submission behavior | 4 | Template 1.5 plus log of unrelated matches; device/IP/email `log2(1 + unrelated hits inside the real window)`. |
| Identity | 4 | Link to an adverse entity 4 × posterior; shared EFT or phone with a different TIN and no common owner 3. |
| Entity age | 1.5 | Under 30 days 1.5; under 90 days 0.75; 0 when the filing is a change of ownership. |

With these weights, BEACON scores: affiliation 5 (EFT; the path explains it, so PageRank is suppressed) + entity age 1.5 → `S = 6.5` → priority ≈ **80**. Contributions: affiliation ≈ 61.8, entity age ≈ 18.5. It sits in the grounds queue, with tier MODERATE shown beside the score.

### 3.3 Apply the time windows the config already names

**Code:** `anomaly/application.py` `record_and_evaluate` (device, IP, and email loops). Config names in `config.py`: `max_submissions_per_ip_7days`, `max_submissions_per_device_7days`, `max_submissions_per_email_30days`.

**What happens now:** Each history entry stores `(application_id, application_date)`. The count ignores the date and includes every prior application with the same device, IP, or email. Three submissions from one IP over three years score the same as three in one week. The evidence text says "within recent submission window", but no window is applied.

**Fix:**

- [ ] Filter device and IP history to 7 days, and email history to 30 days, using the stored submission date.
- [ ] Count only *other* application IDs inside that window against the configured thresholds.

### 3.4 Re-score earlier members when a ring link appears

**Code:** `pipeline/checkpoint1.py` `evaluate_application`. The application is appended to the cohort after scoring. The same look-backward pattern appears in `anomaly/application.py`, `anomaly/template.py` `register_and_evaluate`, `address/clustering.py` `evaluate_density`, and the entity-resolution cohort search.

**What happens now:** Velocity, template, address-density, and entity-resolution checks compare only against applications already evaluated. The first company in a three-company ring keeps a low score after the second and third arrive. Arrival order changes the number.

**Fix:**

- [ ] When a new application links to an earlier one (shared device, template, address, EFT, owner, or high-confidence match), emit a re-score event for the earlier application.
- [ ] Alternatively, score the linked group once and assign that group score to every member.
- [ ] Each re-score writes a **new** audit record that references the previous one. It never edits the old record.
- [ ] Feed human near-match outcomes back into the score: `DECOUPLE` removes the near-match points; `LINK` to an adverse entity becomes a fact.

### 3.5 Give early billing its own score

**Code:** `pipeline/checkpoint2.py`, `claims/ramp_detector.py`, `claims/patient_sharing.py`, `claims/billing_rules.py`, `evaluation/harness.py`.

**What happens now:**

- Checkpoint 2 never updates `priority_score`. Ramp and patient-migration scenarios pass the benchmark because of a boolean flag, while their enrollment score stays at the ACA-tier baseline.
- `has_critical` is computed and never read.
- One post-mortem or impossible-hours count, or any migration evidence, sets `trigger_payment_suspension`. A Critical inpatient-overlap claim on its own does not.
- `BillingRampDetector` falls back to a hard-coded peer cohort when no peers are loaded.
- `cusum_alarm` is just `robust_z > 5`, not a cumulative-sum detector.
- Patient migration fires at 10 or more shared patients, with no share-of-roster test and no timing test.

**Fix:**

- [ ] Keep claims signals out of the enrollment `priority_score`. Add a separate post-enrollment `surveillance_priority_score`.
- [ ] Compare paid amounts with a real peer cohort for the service category. Return "insufficient cohort" when the peer set is too small, and remove the hard-coded fallback.
- [ ] Score month-over-month growth and a real CUSUM.
- [ ] Express hard edits (hours over 24, inpatient overlap, post-mortem) as rates per 1,000 claims, and keep each edit as a deterministic fact.
- [ ] Require a minimum share of the new provider's roster, and the right time order (source sanctioned, then patients appear at the new provider), before raising a migration alert.
- [ ] Emit a CPMM maintenance or revalidation event when the surveillance score crosses a threshold.
- [ ] Replace the automatic `trigger_payment_suspension` with a recommendation that a person evaluate a credible allegation under 42 CFR 455.23.
- [ ] Use `has_critical`, or delete it.

### 3.6 Fix PageRank before using those points

**Code:** `graph/risk_propagation.py` `compute_personalized_pagerank` and `get_risk_score`; `graph/supernodes.py`; `pipeline/checkpoint1.py`.

**What happens now:**

- Restart mass is split evenly across every seed. The same link is worth less as the exclusion list grows. A full LEIE-scale seed list drives the term toward zero.
- `get_risk_score` maps raw PageRank with `(log10(raw + 1e-8) + 5) / 5`. The alert threshold of 0.05 on that scale equals a raw PageRank of about **1.8e-5**, so almost any node connected to a seed fires the alert.
- PageRank is added twice: as a High evidence item (+12) and as continuous points (up to +25).
- Damping multiplies an edge weight by 0.1, and the transition probability then divides by the damped degree. For walks leaving a hub whose edges are all damped, the 0.1 cancels. Walks into the hub are still reduced, so damping only partly works.
- `scan_for_supernodes()` is never called. `is_supernode()` checks live degree with one DB query per edge.
- All edge types use the same base weight. A phone edge carries the same transition probability as a direct-ownership edge.
- Dangling-node handling loops over every seed on every iteration, so runtime grows with the square of the seed count.
- Duplicate seed records create duplicate `LINKED_SEED` edges, which doubles that edge's transition weight (§3.12).

**Fix:**

- [ ] Count a graph path once. Add PageRank points only when no path explains them, and drop the separate PageRank alert item.
- [ ] Express PageRank as a percentile among providers in the same graph snapshot, instead of a fixed cutoff on the log-scaled value.
- [ ] Weight edges by type. Apply supernode damping in a way that survives degree normalization (absorbing hub, or damp only the walk into the hub).
- [ ] Handle dangling mass as one scalar restart, not a loop over every seed.
- [ ] Filter ownership and seed edges by effective and end dates, including in GR-4 (`graph/traversal.py`).
- [ ] Compute PageRank in batch on a graph snapshot (nightly, or on seed refresh) and read the stored value at request time (§3.14).

### 3.7 Score fields the application already carries, and fix entity resolution

**Code:** `pipeline/layer1_rules.py` (`_seeds_by_phone`, `_seeds_by_address`, unused `graph_seed_paths` argument); `pipeline/checkpoint1.py` `_sync_application_to_graph`; `models/entities.py`; `entity_resolution/fellegi_sunter.py` `compare_pair`.

**What happens now:**

- Phone seeds are indexed and never queried. Address seeds are indexed in Layer 1 and never queried there. The graph links address and EFT seeds, but not phone seeds.
- `graph_seed_paths` is passed into `Layer1RulesEngine.evaluate_application` and never read.
- Rendering clinician NPIs become `CLINICIAN` edges and are never scored. `supervising_clinician_npis` is unused.
- These fields are never read by the pipeline: `is_change_of_ownership`, `prior_enrolled_npi`, `has_criminal_record_disclosed`, `parent_organizations`, `practice_locations`, `dba_name`.
- `days_since_formation` returns 999 when a date does not parse. A missing formation date looks like an old company and skips `M4-ANOMALY-RAPID-FORMATION`.
- `compare_pair` defines NPI and TIN weights but never compares them. It computes a person-name similarity that is not added into the match weight. Measured results:

  | Pair | Score | Action |
  |---|---:|---|
  | Same device, dissimilar names | 8.49 | Near-match queue |
  | Same device, similar names | 20.42 | AUTO_LINK |
  | Same name + phone, **different NPI and TIN** | 18.63 | **AUTO_LINK** |

**Fix:**

- [ ] Query phone and address seeds in Layer 1, and add a phone `LINKED_SEED` edge in the graph.
- [ ] Either use `graph_seed_paths` inside Layer 1 or stop passing it.
- [ ] Score shared rendering and supervising clinicians across unrelated TINs, normalized by how many organizations list each clinician.
- [ ] Treat a disclosed criminal conviction (42 CFR 455.106), a change of ownership, and a prior enrolled NPI as facts in their own right.
- [ ] On an unparseable formation date, emit a data-quality evidence item. Do not substitute 999 days.
- [ ] In `compare_pair`, compare NPI and TIN (disagreement should subtract), and include the person-name similarity.
- [ ] Require at least two strong identifiers (NPI, TIN, EFT, SSN4+DOB) agreeing before an auto-link. Show in the explanation how many fields were compared.

### 3.8 Measure ranking in the benchmark

**Code:** `evaluation/harness.py` `is_flagged` (about lines 120–126); `synthetic/generator.py`.

**What happens now:**

- Precision, recall, and F1 are computed from the flag described in §1.
- `review_burden_pct` is the same ratio as the false-positive rate.
- The synthetic set has no legitimate High or Moderate providers, no multi-site group, no credentialing vendor, and no purchased template used by unrelated clinics.
- Scenario labels for ring members can overwrite each other in the scenario dict.

**Fix:**

- [ ] Report precision at a fixed review capacity (precision@K), and ranking quality (PR-AUC) within each ACA tier.
- [ ] Report an ablation with the tier points and Layer 1 points removed, so the remaining score is what gets measured.
- [ ] Add hard negatives:
  - a long-standing home-health agency;
  - a multi-site group with one TIN;
  - a credentialing-vendor device;
  - a purchased policy manual shared by unrelated clinics;
  - dirty address strings.
- [ ] Add invariance tests: seed-list size, arrival order, and organization size must not reorder unrelated clean applications.
- [ ] Keep one scenario result per application ID.

### 3.9 Import bug on Python 3.9–3.13

**Code:** `pipeline/checkpoint1.py` uses `Any` without importing it. `claims/billing_rules.py` uses `Optional` without importing it.

**What happens now:** `pyproject.toml` requires Python `>=3.9`. On 3.14, annotations are deferred, so the import succeeds. On 3.9–3.13, evaluating those annotations raises `NameError`.

**Fix:**

- [ ] Import `Any` in `checkpoint1.py` and `Optional` in `billing_rules.py`.
- [ ] Add CI on the lowest supported Python version.

### 3.10 Scoring must not change state (new)

**Code:** `api/server.py` (`EBVServer.__init__`, `evaluate_enrollment`, `evaluate_claims`); `pipeline/checkpoint1.py` `evaluate_application` and `_sync_application_to_graph`; `graph/store.py` `add_edge`.

**What happens now:**

- `POST /api/evaluate/enrollment` permanently writes the application into the graph, the cohort, and the velocity and template histories. A "what-if" request becomes evidence against later applicants.
- `add_edge` is a plain `INSERT` with no uniqueness rule. Scoring the same application four times left **16 edges** from its NPI (4 per call). The score did not change in that test, but duplicate edges distort PageRank and density over time.
- The API builds `application_id` as `APP-{last 6 digits of NPI}`. Different NPIs with the same suffix collide, and a resubmission is not recognized as one.
- At startup the server evaluates the first 10 synthetic applications into the live state.
- The audit `snapshot_id` is hard-coded (`SNAP-2026-02-01`), so a score cannot be tied to the graph state it used.

**Fix:**

- [ ] Split into two operations:
  - `score(application, snapshot_id)` is read-only and returns the decision package.
  - `ingest(application_event)` writes to the graph and histories, with an idempotency key.
- [ ] Add a unique index on `(source_id, target_id, edge_type, effective_start)` and upsert edges.
- [ ] Use the State's application ID (or a UUID) plus an idempotency key, not an ID derived from the NPI.
- [ ] Record the real snapshot ID and the weights version in every audit record.
- [ ] Remove the synthetic warm-up from server start; load demo state only in demo mode.

### 3.11 Make the audit chain actually append-only (new)

**Code:** `pipeline/audit.py`.

**What happens now:**

- `record_reviewer_override` sets `reviewer_id`, `reviewer_action`, and `reviewer_notes` on the existing record. Measured: after an override the stored `audit_hash` is unchanged, so the chain cannot detect the edit.
- The chained content covers ID, time, feature hash, status, and score. It leaves out the weights or config version, evidence severities, and contributions.
- `compute_feature_vector_sha256` covers some inputs but not disclosed persons, device, IP, or template hash.
- `model_version` is a constructor default (`v1.0.0-phase0`). Records are held in memory only, and there is no `verify_chain()`.

**Fix:**

- [ ] Write overrides as new chained events (`REVIEWER_DECISION`) that reference the original record ID. Never mutate a stored record.
- [ ] Include in the hash: config/weights hash, snapshot ID, evidence codes with severities, and contributions.
- [ ] Extend the feature hash to cover every input field the pipeline reads.
- [ ] Persist to immutable storage (for example, a WORM-policy blob container), and add `verify_chain()` plus a scheduled verification job.

### 3.12 Seed-list hygiene (new)

**Code:** `synthetic/generator.py` (seed construction); `pipeline/checkpoint1.py` (seed loading); `graph/store.py` `add_node`.

**What happens now:**

- Duplicate seeds: `SEED-004` and `SEED-EFT-REENTRY` hold the same EFT, and `SEED-002` and `SEED-SUSPENDED-PROVIDER-X` hold the same NPI. The BEACON trace shows the duplicate `LINKED_SEED` edge.
- `add_node` uses `INSERT OR REPLACE`. When an application's NPI equals an existing seed node ID, the provider node replaces the seed node and `is_seed` is cleared.
- Seed `end_date` (reinstatement) is not applied when matching.

**Fix:**

- [ ] Normalize and deduplicate seeds by `(identifier_type, normalized_value)`. Keep every source as provenance on the one merged seed.
- [ ] Merge attributes when an application node collides with a seed node. Do not clear `is_seed`.
- [ ] Apply `effective_date` and `end_date` at match time, as of the application date.
- [ ] Track the refresh time for each source (LEIE monthly, SAM daily, state lists). Keep state seeds per jurisdiction; only federal seeds are shared across states.

### 3.13 Separate verified facts from observed patterns (new)

**Code:** `anomaly/application.py`, `anomaly/template.py`, `address/clustering.py`, `claims/ramp_detector.py`, `claims/patient_sharing.py` all set `verified_fact=True`.

**What happens now:** Statistical observations (velocity counts, MinHash similarity, address density, robust z-scores, patient overlap) are labelled as verified facts. A reviewer cannot tell a record match ("this EFT is on the termination list") from a pattern ("this manual is 0.83 similar to another").

**Fix:**

- [ ] Set `verified_fact=True` only for record equality against an authoritative source (seed match, USPS CMRA flag, date-of-death record, hard billing edit).
- [ ] Mark every other item as a pattern and show the measured value and threshold beside it.

### 3.14 PageRank and graph cost (new)

**Code:** `pipeline/checkpoint1.py` (PageRank per application); `graph/supernodes.py` `is_supernode`.

**What happens now:** Every enrollment request recomputes personalized PageRank over the whole graph, and `is_supernode` runs a database query for each edge. Request latency grows with total graph size.

**Fix:**

- [ ] Compute PageRank in batch per snapshot and store the result per node. At request time, read the stored value, or run a bounded local push from the new node's neighborhood.
- [ ] Precompute node degree once per snapshot.

### 3.15 Make the benchmark independent of the detectors (new)

**Code:** `synthetic/generator.py`; `evaluation/harness.py`.

**What happens now:** The scenarios were written by the same author as the detectors, to show that each detector fires. 100% recall on that set is circular. There is no typology the detectors were not designed for, and no realistic legitimate population.

**Fix:**

- [ ] Write a **typology catalog** from public sources only (DOJ indictments, OIG reports, CMS statistics, CFR). Include each typology's observable signals and variations, plus matching hard negatives.
- [ ] Build the simulator from the catalog, with randomized parameters and noise (typos, missing fields, legitimate reuse). Keep the authors apart, or at least keep the catalog fixed before detector changes.
- [ ] Hold out at least one typology that no detector targets, and report what the system misses.
- [ ] Report recall per typology, precision@K at reviewer capacity, and score-band hit rates.
- [ ] The simulator must not be fitted to, or parameterized from, State Data (S1: synthetic data derived from State Data counts as State Data).

### 3.16 Calibration, capacity, drift, and fairness monitoring (new)

**What happens now:** Levels are fixed point cutoffs. There is no link to how many cases reviewers can handle, no score-distribution monitoring, and no cohort-level review (NIST AI RMF; handoff §5.4).

**Fix:**

- [ ] Set band thresholds from reviewer capacity: for example, the top N per week go to the investigative queue, and the next band to desk review.
- [ ] Monitor weekly: score distribution (PSI), alert rate per tier, category, county, and applicant age, and evidence-code frequencies.
- [ ] Monitor fairness: alert and adverse-outcome rates for small or new entrants, rural providers, and other cohorts the State defines. Investigate gaps before changing weights.
- [ ] Capture reviewer dispositions using the S8 taxonomy. Treat the labels as positive-unlabeled: "not investigated" does not mean "legitimate".
- [ ] Use point-in-time features, so evaluation never uses information that came after the decision date.

### 3.17 Model governance before any ML (new)

**What happens now:** Weights are constants in code. There is no version registry, model card, State sign-off step, or shadow mode.

**Fix:**

- [ ] Version every scorer: config hash, weights table, code commit, and evaluation report.
- [ ] Use a promotion path: **shadow** (scored and logged, not shown) → **advisory** (shown to reviewers) → **rule input**. Each step needs State review of that version (Attachment C §5(d)/§7).
- [ ] Keep a champion/challenger setup and one-step rollback.
- [ ] Before fitting any model on State Data, including unsupervised ones (Isolation Forest, DBSCAN, EM-estimated Fellegi–Sunter weights), get a written contract ruling. Supervised models also need roughly 500–1,000 adjudicated PI outcomes.

---

## 4. Do not change

- Layer 1 as the only place that records statutory facts and assigns the ACA tier under 42 CFR 455.450 and 455.416.
- The evidence-item model, the "pattern requires review" wording, and the SHA-256 audit chain concept (fixed per §3.11).
- Fellegi–Sunter expert weights and the human near-match queue (`LINK`, `VOID`, `END_DATE`, `DECOUPLE`).
- The address allowlist for FQHC, CHC, tribal health, and hospital campus.
- Hard billing edits as facts (hours over 24, inpatient overlap, services after death).
- Expert-set weights instead of a model trained on State enrollment or claims data (NC RFP Attachment C §3(e)(ii)), until a written ruling and enough labels exist.

---

## 5. Suggested order

| Phase | Items | Why first |
|---|---|---|
| **A. Correctness** | 3.9 imports; 3.10 read-only scoring, edge upsert, IDs; 3.12 seed dedupe and overwrite; 3.11 append-only audit; 3.3 time windows | Every later measurement is wrong while state leaks and seeds duplicate. |
| **B. Score design** | 3.1 Layer 1 out of the score, status rename; 3.2 fact keys, families, saturating combination; 3.6 PageRank once; 3.13 fact vs pattern | Turns the tally into a ranker whose contributions are explainable. |
| **C. Coverage** | 3.7 unused fields and entity-resolution fixes; 3.4 ring re-scoring; 3.5 separate surveillance score | Adds signal only after double counting is gone. |
| **D. Evaluation** | 3.8 ranking metrics and ablation; 3.15 typology catalog, independent simulator, held-out typology; invariance tests | Gives a trustworthy number to tune weights against. |
| **E. Operations** | 3.14 batch PageRank; 3.16 capacity bands and monitoring; 3.17 versioning, shadow mode, State review | Needed before the score is shown to reviewers on live data. |

---

## Appendix — How the v2 findings were checked

All checks ran on Python 3.14.8, read-only (`PYTHONDONTWRITEBYTECODE=1`), against an in-memory pipeline.

| Finding | Check | Result |
|---|---|---|
| §2.1 stacking | Scenario 3 (BEACON) after the server's 10-application warm-up | Contributions sum to 128.1; displayed 100 / CRITICAL; 122.1 points from one EFT |
| §3.6 / §3.12 duplicate seed edge | Edge listing for the BEACON EFT node | Two identical `LINKED_SEED` edges |
| §3.7 entity resolution | `FellegiSunterResolver.compare_pair` on constructed pairs | 8.49 queue / 20.42 auto-link / 18.63 auto-link with different NPI and TIN |
| §3.10 state mutation | Same application evaluated 4 times | Score stable at 0, 16 edges from its NPI |
| §3.11 audit | `record_reviewer_override` on the last record | `reviewer_action` stored in place; `audit_hash` unchanged |
| §3.12 overwrite | `graph/store.py` `add_node` | `INSERT OR REPLACE` |
| §3.6 supernodes | Search for callers of `scan_for_supernodes` | Defined only, never called |
| §3.6 threshold | `(log10(raw) + 5) / 5 = 0.05` | raw ≈ 1.78e-5 |
