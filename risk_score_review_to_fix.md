# Risk Score Review — Items to Fix

| Document Metadata | Specification |
|---|---|
| **Date** | 2026-11-18 |
| **Scope** | Layer 2 priority score, the signals that feed it, Checkpoint 2 actions, and the benchmark that reports score quality |
| **Status** | **Open — to be fixed** |
| **Governance** | ADR-0001: the score stays assistive. It must not deny, approve, or set the ACA risk tier. |

---

## 1. Verdict

The 0–100 `priority_score` is a tally of evidence points. It is a weak ranker outside the synthetic demo.

The published 100% precision and recall do not measure ranking. The harness marks an application as flagged when Layer 1 status is `REVIEW_REQUIRED` or `DENIED_GROUNDS`, when the label is `HIGH` or `CRITICAL`, or when Checkpoint 2 sets a site-visit or payment-suspension flag. Every Moderate or High ACA category is already `REVIEW_REQUIRED`, so the benchmark passes before the numeric score does any ordering.

Two independent reviews of the scoring path agreed on the defects below. The behaviors were then confirmed in source.

---

## 2. How the score works today

`Layer2AdvisoryEngine.compute_advisory_package` in `src/ebv_detector/pipeline/layer2_advisory.py` adds three terms and caps the total at 100:

| Term | Points | Where |
|---|---|---|
| Layer 1 already found statutory grounds (`DENIED_GROUNDS`) | +50 | `layer2_advisory.py` |
| Else ACA tier High / Moderate | +25 / +10 | same |
| Each evidence item by severity: Critical / High / Medium / Low | +20 / +12 / +6 / +2 | same |
| Near-match items only | +5 each, cap 10 | same |
| Personalized PageRank proximity | up to +25 | `graph/risk_propagation.py` `get_risk_score` |

`composite_risk_level` does not follow that number. Any statutory ground, or any single Critical evidence item, forces `CRITICAL` and recommends a Program Integrity referral plus a prepayment hold.

`feature_contributions` are recorded before the cap. A reviewer can see contributions that sum past 120 while the displayed score is 100.

### Same fact counted several times

One bank account shared with a terminated provider is scored as all of the following:

1. +50 because Layer 1 status is `DENIED_GROUNDS`
2. +20 for the Layer 1 verified fact (`L1-SEED-EFT-SHARED-TERMINATION`)
3. +20 for the graph path (`M3-GRAPH-PATH-TO-SANCTIONED-SEED`, Critical when hops ≤ 2)
4. +12 for the PageRank alert item (`M3-PPR-SEED-PROXIMITY-ALERT`)
5. up to +25 again from the continuous PageRank term

A copied policy manual adds +12 once per earlier matching application (`M4-TEMPLATE-POLICY-CLONE`). The tenth copy can exceed 100 points from templates alone.

---

## 3. Fixes

### 3.1 Take Layer 1 out of the queue score

**Code:** `pipeline/layer2_advisory.py` (base points), `pipeline/layer1_rules.py` (`evaluate_application` status, about lines 211–220).

**What happens now:** Statutory hits and the ACA category are added into the same 0–100 number used to order the review queue. Denied cases tie at the top. A clean home-health, HCBS, DME, hospice, NEMT, or personal-care provider starts at 25. A clean behavioral-health or ABA provider starts at 10. One extra medium finding can push a clean high-tier provider into `HIGH` and the fingerprint / bank-proof action list.

Layer 1 also emits `DENIED_GROUNDS` as soon as a statutory ground exists. Adverse determinations are supposed to stay with a person.

**Fix:**

- [ ] Keep `Layer1Decision` as the statutory record: grounds, ACA tier, and required screening actions (42 CFR 455.432 / 455.434).
- [ ] Stop adding `layer1_statutory_grounds` (+50), Layer 1 verified-fact points, and `aca_tier_high` / `aca_tier_moderate` into `priority_score`.
- [ ] Route applications with statutory grounds to their own queue. Use `priority_score` to rank applications that still need investigative ordering.
- [ ] Show the ACA tier beside the score. Do not add it in.
- [ ] Rename the automatic status so it records grounds pending a human decision, rather than a completed denial.
- [ ] Derive `composite_risk_level` from the deduplicated score. Reserve `CRITICAL` for verified statutory or seed facts, not for a single device-sharing item.
- [ ] Make recommended actions follow the evidence type (site visit for address, bank-ownership proof for a shared EFT). Stop recommending a prepayment hold from the enrollment score alone.
- [ ] Make `feature_contributions` sum to the score that is displayed.

### 3.2 Score each underlying fact once, with a cap per family

**Code:** `pipeline/layer2_advisory.py` severity weights; evidence emitted from `pipeline/checkpoint1.py`, `anomaly/template.py`, `address/rules.py`.

**What happens now:** Every evidence item adds its full severity weight. Correlated detectors for one identifier (Layer 1 fact, graph path, PageRank item, PageRank points) all add. Template and high-confidence entity-resolution links add once per prior application, with no cap except near-matches.

A shared device fingerprint is `CRITICAL` by itself (`anomaly/application.py`), which forces the fraud-referral recommendation even when the device belongs to a credentialing vendor.

**Fix:**

- [ ] Partition signals into non-overlapping families: affiliation to a known-bad seed, ownership, address, submission behavior, and identity overlap.
- [ ] Inside a family, apply diminishing returns: full weight for the strongest hit, then half, then a quarter, with a family cap.
- [ ] Give one shared EFT, one mailbox address, and one policy template a single fact key so they cannot be stacked.
- [ ] Score template similarity from the strongest match, or from `log2(1 + unrelated matches)`, not from +12 per pair.
- [ ] Down-weight a template shared by many unrelated clean entities.
- [ ] Treat a shared device, IP, or email as points only when the other applications have a different TIN and no common owner. Same-organization reuse goes to the near-match queue (PDM-REQ-DATA-025 / PDM-REQ-SEC-052), not to the fraud score.
- [ ] Keep weights in `config.py` and hash the weight version into the audit record.

Suggested starting caps, in relative weight units, not trained on state data:

| Family | Cap | Starting weights |
|---|---|---|
| Affiliation | 6 | Active adverse seed by EFT or SSN 5; phone 3; device 2.5; same-suite address 2; shared clinician 1.5. Halve at 3 hops. PageRank only when there is no path. |
| Ownership | 5 | Excluded upstream owner below 5% on dated edges 2.5; disclosed parent with no ownership edge 2. |
| Address | 4 | One collapsed mailbox item 3; vacant 2; unrelated entities in the same building `log2(n/3)`, max 3. |
| Submission behavior | 4 | Template 1.5 plus log of unrelated matches; device/IP/email `log2(1 + unrelated hits inside the real window)`. |
| Identity | 4 | Link to an adverse entity 4 × posterior; shared EFT or phone with a different TIN and no common owner 3. |
| Entity age | 1.5 | Under 30 days 1.5; under 90 days 0.75; 0 when the filing is a change of ownership. |

### 3.3 Apply the time windows the config already names

**Code:** `anomaly/application.py` `record_and_evaluate` (device, IP, and email loops). Config names in `config.py`: `max_submissions_per_ip_7days`, `max_submissions_per_device_7days`, `max_submissions_per_email_30days`.

**What happens now:** Each history entry stores `(application_id, application_date)`. The count drops the date and keeps every prior application with the same device, IP, or email. Three submissions from one IP over three years score the same as three in a week. The evidence text says "within recent submission window" even though no window is applied.

**Fix:**

- [ ] Filter device and IP history to 7 days, and email history to 30 days, using the stored submission date.
- [ ] Count only other application IDs inside that window against the configured thresholds.

### 3.4 Re-score earlier members when a ring link appears

**Code:** `pipeline/checkpoint1.py` `evaluate_application`. Cohort append happens after scoring. The same look-backward pattern is in `anomaly/application.py`, `anomaly/template.py` `register_and_evaluate`, `address/clustering.py` `evaluate_density`, and `entity_resolution` cohort search.

**What happens now:** Velocity, templates, address density, and entity resolution compare only with applications already evaluated. The first company in a three-company ring stays at a low score after the second and third arrive. Arrival order changes the number.

**Fix:**

- [ ] When a new application links to an earlier one (shared device, template, address, EFT, owner, or high-confidence match), recompute the earlier advisory scores.
- [ ] Alternatively, score the linked group once and assign that group score to every member.
- [ ] Read human near-match outcomes back into the score: `DECOUPLE` suppresses the near-match points; `LINK` to an adverse entity becomes a fact.

### 3.5 Give early billing its own score

**Code:** `pipeline/checkpoint2.py`, `claims/ramp_detector.py`, `claims/patient_sharing.py`, `claims/billing_rules.py`, `evaluation/harness.py`.

**What happens now:** Checkpoint 2 never updates `priority_score`. Ramp and patient-migration scenarios pass the benchmark because of a boolean flag, while their enrollment score stays at the ACA-tier baseline (10 for a Moderate category).

`has_critical` is computed in `evaluate_provider_early_billing` and never read. A single post-mortem or impossible-hours count, or any migration evidence list, sets `trigger_payment_suspension`. An inpatient-overlap claim that is Critical in the evidence list does not, unless another branch already fired.

`BillingRampDetector` falls back to a hard-coded peer cohort when no peers are loaded. `cusum_alarm` is `robust_z > 5`, not a cumulative-sum detector, and it is not scored. Patient migration fires at 10 or more shared patients with no share-of-roster and no timing test (patients moving after the source provider was suspended).

**Fix:**

- [ ] Keep claims signals off the enrollment `priority_score`.
- [ ] Add a separate post-enrollment `surveillance_priority_score`.
- [ ] Compare paid amounts to a real peer cohort for the service category. Return "insufficient cohort" when the peer set is too small. Remove the hard-coded fallback.
- [ ] Score month-over-month growth and a real CUSUM, not a second z-score threshold.
- [ ] Express hard edits (hours over 24, inpatient overlap, post-mortem) as rates per 1,000 claims, and keep each edit as a deterministic fact.
- [ ] Require a minimum share of the new provider's roster and a time order (source sanctioned, then patients appear on the new provider) before a migration alert.
- [ ] Emit a CPMM maintenance or revalidation event when the surveillance score crosses a threshold.
- [ ] Replace the automatic `trigger_payment_suspension` flag with a recommendation that a person evaluate a credible allegation under 42 CFR 455.23.
- [ ] Use `has_critical`, or delete it so the action rules match the evidence severities.

### 3.6 Fix PageRank before using those points

**Code:** `graph/risk_propagation.py` `compute_personalized_pagerank` and `get_risk_score`; `graph/supernodes.py`; `pipeline/checkpoint1.py` (PageRank is recomputed on every application).

**What happens now:**

- Restart mass is split evenly across every seed. The same link is worth less as the exclusion list grows, and a full LEIE-scale seed list drives the term toward zero.
- `get_risk_score` maps raw PageRank with `(log10(raw + 1e-8) + 5) / 5`. Very small raw values still clear `ppr_alert_threshold` (0.05).
- Damping multiplies an edge by 0.1 and the transition probability then divides by the damped degree. On a hub whose edges are all damped, the 0.1 cancels.
- `scan_for_supernodes()` is never called. `is_supernode()` can still damp by live degree, and that path has the cancellation above.
- All edge types use the same base weight. A phone edge and a direct-ownership edge carry the same transition probability.
- Dangling-node handling loops over every seed on every iteration, so runtime grows with the square of the seed count.
- `_sync_application_to_graph` writes a new provider node at the NPI id. If that NPI was already a seed node, the seed flag is replaced.

**Fix:**

- [ ] Count a graph path once. Add PageRank points only when there is no explaining path.
- [ ] Compare a provider's PageRank with other providers in the same graph snapshot (a percentile), instead of a fixed 0.05 cutoff on the log-scaled value.
- [ ] Weight edges by type. Apply supernode damping in a way that survives degree normalization (absorbing hub, or damp only the walk into the hub).
- [ ] Call supernode detection on the current graph before propagation, or rely on one degree check that matches the damping math.
- [ ] Handle dangling mass as one scalar restart, not a loop over every seed.
- [ ] Merge seed attributes when an application NPI collides with an existing seed node. Do not clear `is_seed`.
- [ ] Filter ownership and seed edges by effective and end dates, including in GR-4 (`graph/traversal.py`).

### 3.7 Score fields the application already carries

**Code:** `pipeline/layer1_rules.py` (`_seeds_by_phone`, `_seeds_by_address`, unused `graph_seed_paths` argument); `pipeline/checkpoint1.py` `_sync_application_to_graph`; `models/entities.py`.

**What happens now:**

- Phone seeds are indexed and never queried. Address seeds are indexed in Layer 1 and never queried there. The graph links address and EFT seeds and does not link phone seeds.
- `graph_seed_paths` is passed into `Layer1RulesEngine.evaluate_application` and never read.
- Rendering clinician NPIs become `CLINICIAN` edges and are never scored. `supervising_clinician_npis` is unused.
- `is_change_of_ownership`, `prior_enrolled_npi`, `has_criminal_record_disclosed`, `parent_organizations`, `practice_locations`, and `dba_name` are unused.
- `days_since_formation` returns 999 when the date does not parse, so a missing formation date looks like an old company and skips `M4-ANOMALY-RAPID-FORMATION`.
- `FellegiSunterResolver.compare_pair` defines weights for NPI and TIN and computes a person-name similarity that is not added into the match weight. A shared device alone can clear the auto-link threshold. Missing fields contribute 0 rather than uncertainty.

**Fix:**

- [ ] Query phone and address seeds in Layer 1, and add a phone `LINKED_SEED` edge in the graph.
- [ ] Either use `graph_seed_paths` inside Layer 1 or stop passing it.
- [ ] Score shared rendering and supervising clinicians across unrelated TINs, normalized by how many organizations list that clinician.
- [ ] Treat a disclosed criminal conviction (42 CFR 455.106), a change of ownership, and a prior enrolled NPI as their own facts.
- [ ] On an unparseable formation date, emit a data-quality evidence item. Do not substitute 999 days.
- [ ] In `compare_pair`, compare NPI and TIN, include the person-name similarity, and avoid letting one field (device or phone) auto-link when the other identifiers are absent.

### 3.8 Measure ranking in the benchmark

**Code:** `evaluation/harness.py` `is_flagged` (about lines 120–126); `synthetic/generator.py`.

**What happens now:** Precision, recall, and F1 are computed from the flag above. `review_burden_pct` is the same ratio as the false-positive rate. The synthetic set has no legitimate High or Moderate providers, no multi-site group, no credentialing vendor, and no purchased template used by unrelated clinics. Scenario labels for ring members can overwrite each other in the scenario dict.

**Fix:**

- [ ] Report precision at a fixed review capacity, and ranking quality within each ACA tier.
- [ ] Report an ablation with the tier points and Layer 1 points removed, so the remaining score is what gets measured.
- [ ] Add hard negatives: a long-standing home-health agency, a multi-site group with one TIN, a credentialing vendor device, a shared purchased policy manual, and dirty address strings.
- [ ] Add invariance checks: seed-list size, arrival order, and organization size must not reorder unrelated clean applications.
- [ ] Keep one scenario result per application id.

### 3.9 Import bug on Python 3.9–3.13

**Code:** `pipeline/checkpoint1.py` uses `Any` in `Checkpoint1EvaluationPackage.near_matches` and does not import it. `claims/billing_rules.py` uses `Optional` and does not import it.

**What happens now:** `pyproject.toml` requires Python `>=3.9`. On 3.14, annotations are deferred, so import succeeds. On 3.9 through 3.13, evaluating those annotations raises `NameError`.

**Fix:**

- [ ] Import `Any` in `checkpoint1.py` and `Optional` in `billing_rules.py`.

---

## 4. Do not change

- Layer 1 as the only place that records statutory facts and assigns the ACA tier under 42 CFR 455.450 and 455.416.
- The evidence-item model, the "pattern requires review" wording, and the SHA-256 audit chain.
- Fellegi–Sunter expert weights and the human near-match queue (`LINK`, `VOID`, `END_DATE`, `DECOUPLE`).
- Address allowlist for FQHC, CHC, tribal health, and hospital campus.
- Hard billing edits as facts (hours over 24, inpatient overlap, services after death).
- The rule that this score is not replaced by a model trained on state enrollment or claims data (NC RFP Attachment C §3(e)(ii)).

---

## 5. Suggested order

1. Remove Layer 1 and ACA-tier points from `priority_score`, and make the level follow the remaining score.
2. Cap and deduplicate evidence families so one fact cannot saturate the score.
3. Apply the 7-day and 30-day windows, and re-score earlier ring members.
4. Add the separate Checkpoint 2 surveillance score and stop auto-setting payment suspension.
5. Fix PageRank scaling, seed-node overwrite, and dated ownership edges.
6. Wire the unused phone, clinician, ownership-change, and formation-date signals.
7. Replace the benchmark flag with ranking metrics and hard negatives.
8. Add the missing typing imports.
