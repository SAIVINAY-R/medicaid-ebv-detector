# Phase 0 Research Spike S2 — Data Inventory & Access Map

| Field | Value |
|---|---|
| **Author** | Senior ML Engineer |
| **Date** | 2026-11-11 |
| **Scope** | State Deployments: North Carolina (NC), North Dakota (ND), New Hampshire (NH) |
| **Standard** | ADR-0001, HIPAA ARC-AMPE, NIST 800-53 |

---

## 1. Internal CPMM Data Assets

| Asset Name | Description | Source System | PII Classification | Ingestion Cadence | Primary Fraud Signals |
|---|---|---|---|---|---|
| **Provider Applications** | Legal name, DBA, TIN, NPI, taxonomy, formation date | Dataverse (`cpmm_providerapplication`) | Sensitive / PII | Real-time event | Rapid formation, taxonomy steering |
| **Disclosed Individuals** | 5%+ direct owners, officers, managing employees, SSN last 4, DOB | Dataverse (`cpmm_personneldisclosure`) | High Sensitive (SSN/DOB) | Real-time event | Straw nominee owners, excluded persons |
| **Locations & Addresses** | Physical practice locations, billing addresses, suites | Dataverse (`cpmm_location`) | Standard PII | Real-time event | Mailbox stores (CMRA), PMB, residential vs facility mismatch |
| **EFT Banking Details** | Bank routing number, account number, institution name | Dataverse (`cpmm_eftaccount`) | Highly Confidential (Financial) | Real-time event | Shared bank accounts with prior suspended/terminated providers |
| **Device & IP Telemetry** | Browser device fingerprint, public submission IP, session duration | Azure APIM Gateway Logs | Technical / Metadata | Real-time event | High submission velocity, coordinated applicant mills |
| **Policy Manual Narratives** | Clinical SOPs, crisis plans, compliance manuals | Azure Blob / Dataverse Notes | Confidential Business | Real-time event | MinHash templated policy manual cloning across rings |
| **Adjudication Decisions** | Reviewer actions on near-matches (LINK, VOID, END_DATE) | CPMM Queue (`cpmm_nearmatchqueue`) | Internal Audit | Near real-time | Ground truth labels, verified relationship links |

---

## 2. External Verification & Seed Feeds

| External Source | Authority | Refresh Cadence | Interface Mechanism | Target Pipeline Component | Legal / Agreement Anchor |
|---|---|---|---|---|---|
| **OIG LEIE** | HHS Office of Inspector General | Monthly | HTTPS Flat-File / CSV Batch | Layer 1 Seed Bank, M3 Graph | Public Federal Record (42 CFR 455.436) |
| **SAM.gov Exclusions** | GSA System for Award Management | Daily / Monthly | SAM API / S3 Extract | Layer 1 Seed Bank, M3 Graph | Public Federal Record (42 CFR 455.436) |
| **Social Security DMF** | SSA Death Master File | Quarterly (Mandatory 2028 under OBBBA §71105) | NTIS Certified SFTP Batch | Layer 1 Deceased Checks, CP2 Ghost Claims | NTIS Certification Required |
| **NPPES Registry** | CMS National Plan & Provider Enumeration | Weekly | CMS Bulk NPI Data Extract | M1 Entity Resolution, Identity Corroboration | Public CMS Data |
| **State SoS Business Registry** | Secretary of State Corporations Division | Daily / Batch | State API (NC SoS / ND SoS / NH SoS) | M4 Entity Age, Officer Corroboration | Public Registry |
| **Melissa CASS / DPV / CMRA** | USPS Certified Coding Address Accuracy | Real-Time API | Private APIM Adapter | M2 Address Hygiene, DATA-039 Prohibitions | ADR-0001 Accepted Architecture |
| **LexisNexis Provider Integrity** | Commercial Identity & Corporate Aggregator | Batch / On-Demand | Private APIM Connector | M1 Resolver, Corporate Affiliations | ND SIP Contract, FCRA Review |

---

## 3. Checkpoint 2 Claims Feeds (MMIS / PI Interface)

| Feed | Source Platform | Latency | Protocol | Key Fields | Anomaly Detection Target |
|---|---|---|---|---|---|
| **Fee-For-Service (FFS) Claims** | State MMIS (NCTracks / ND MMIS) | Daily / Weekly | Azure Event Hub / SFTP | NPI, Rendering NPI, Beneficiary ID, Date, Procedure, Paid Amount, Units | Billing ramps (robust z-score), impossible clinician hours (>24h/day) |
| **Inpatient Hospital Stays** | MMIS Institutional Inpatient Encounters | Daily | Azure Synapse / SQL View | Beneficiary ID, Admission Date, Discharge Date | CP2 Inpatient Overlap (billing community care while admitted) |
| **EVV Visit Telemetry** | State EVV Aggregator (Personal Care / Home Health) | Daily Batch | REST API / JSON | Caregiver NPI, Beneficiary ID, Geolocation, Check-in/out Timestamps | Ghost visits, impossible travel, overlapping visits |

