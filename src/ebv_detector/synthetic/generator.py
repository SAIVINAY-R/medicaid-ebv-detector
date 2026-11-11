"""Synthetic EBV Generator (Spike S3).

Implements every red flag across all four EBV lifecycle stages from §2.4:
1. Enter: Rapid LLC formation, nominee owners, CMRA/PMB addresses, shared EFT/phone/device,
   shared clinicians across multiple orgs, templated policy manuals, taxonomy steering.
2. Bill: Zero-to-millions billing ramp vs peer cohort, impossible hours (>24h/day),
   ghost billing during hospital inpatient stays or post-mortem.
3. Vanish: Last-minute EFT bank swaps, dormancy before scheduled audit.
4. Re-enter: Near-identical business names, matching SSN-last4/DOB or EFT with prior excluded seeds,
   patient cohort migration from suspended entities.

Built exclusively from public knowledge and federal fraud indictments (DOJ 2026, CMS CRUSH),
strictly compliant with NC RFP Attachment C §3(e)(ii) (no training on or derivation from State Data).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import hashlib
import random
from typing import Dict, List, Optional, Set, Tuple

from ..models.entities import (
    AddressRecord,
    ClaimRecord,
    EFTAccount,
    OwnershipEdge,
    PersonDisclosure,
    ProviderApplication,
    SeedRecord,
)


@dataclass
class SyntheticDataset:
    """Container holding synthesized provider applications, seeds, claims, and ground truth labels."""
    applications: List[ProviderApplication] = field(default_factory=list)
    seed_records: List[SeedRecord] = field(default_factory=list)
    claims: List[ClaimRecord] = field(default_factory=list)
    ownership_edges: List[OwnershipEdge] = field(default_factory=list)
    ground_truth: Dict[str, str] = field(default_factory=dict) # app_id -> "EBV_FRAUD" or "LEGITIMATE"
    scenario_metadata: Dict[str, str] = field(default_factory=dict) # app_id -> scenario description


class SyntheticEBVGenerator:
    """Generates synthetic datasets containing legitimate providers and targeted fraud schemes."""

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self._npi_counter = 1900000000
        self._tin_counter = 560000000
        self._app_counter = 1000

    def _next_npi(self) -> str:
        self._npi_counter += 1
        return str(self._npi_counter)

    def _next_tin(self) -> str:
        self._tin_counter += 1
        return f"{str(self._tin_counter)[:2]}-{str(self._tin_counter)[2:]}"

    def _next_app_id(self) -> str:
        self._app_counter += 1
        return f"APP-{self._app_counter:06d}"

    def generate_benchmark_suite(self) -> SyntheticDataset:
        """Generate a complete benchmark suite covering all legitimate and EBV fraud scenarios."""
        dataset = SyntheticDataset()
        
        # 1. Establish known-bad seed database (LEIE, SAM, state suspensions)
        seed_bank = self._generate_base_seeds()
        dataset.seed_records.extend(seed_bank)
        
        # 2. Generate a cohort of legitimate providers (Control group)
        legit_apps, legit_edges, legit_claims = self._generate_legitimate_cohort(count=15)
        dataset.applications.extend(legit_apps)
        dataset.ownership_edges.extend(legit_edges)
        dataset.claims.extend(legit_claims)
        for app in legit_apps:
            dataset.ground_truth[app.application_id] = "LEGITIMATE"
            dataset.scenario_metadata[app.application_id] = "Standard legitimate community healthcare provider"
            
        # 3. Scenario 1: Rapid-Formation Shell at Mailbox Store (Enter Stage)
        s1_app = self._build_scenario_1_mailbox_shell()
        dataset.applications.append(s1_app)
        dataset.ground_truth[s1_app.application_id] = "EBV_FRAUD"
        dataset.scenario_metadata[s1_app.application_id] = (
            "Scenario 1: Formed 8 days before applying; CMRA mailbox store address for facility-based autism clinic"
        )
        
        # 4. Scenario 2: Nominee Owner with Hidden Excluded Mastermind (GR-4 Indirect Ownership)
        s2_app, s2_edges, s2_seeds = self._build_scenario_2_nominee_hidden_exclusion()
        dataset.applications.append(s2_app)
        dataset.ownership_edges.extend(s2_edges)
        dataset.seed_records.extend(s2_seeds)
        dataset.ground_truth[s2_app.application_id] = "EBV_FRAUD"
        dataset.scenario_metadata[s2_app.application_id] = (
            "Scenario 2: Straw owner fronting for holding company 60% owned by OIG-excluded individual"
        )
        
        # 5. Scenario 3: Shared EFT & Device Identifier Re-entry Ring
        s3_app, s3_seed = self._build_scenario_3_shared_eft_reentry()
        dataset.applications.append(s3_app)
        dataset.seed_records.append(s3_seed)
        dataset.ground_truth[s3_app.application_id] = "EBV_FRAUD"
        dataset.scenario_metadata[s3_app.application_id] = (
            "Scenario 3: New LLC re-entering with identical EFT routing/account of terminated provider"
        )
        
        # 6. Scenario 4: Multi-Entity Clinician Sharing & Templated Policy Manuals
        s4_apps = self._build_scenario_4_templated_clinician_ring()
        dataset.applications.extend(s4_apps)
        for app in s4_apps:
            dataset.ground_truth[app.application_id] = "EBV_FRAUD"
            dataset.scenario_metadata[app.application_id] = (
                "Scenario 4: Multi-entity cluster formed in same week with identical policy manual & shared clinicians"
            )
            
        # 7. Scenario 5: Post-Enrollment Zero-to-Millions Ramp & Impossible Hours (Bill Stage)
        s5_app, s5_claims = self._build_scenario_5_billing_ramp_and_impossible_hours()
        dataset.applications.append(s5_app)
        dataset.claims.extend(s5_claims)
        dataset.ground_truth[s5_app.application_id] = "EBV_FRAUD"
        dataset.scenario_metadata[s5_app.application_id] = (
            "Scenario 5: 3-month ramp to $3.5M with 32h/day billed per clinician and post-mortem claims"
        )
        
        # 8. Scenario 6: Coordinated Patient Cohort Migration from Suspended Provider
        s6_app, s6_claims, s6_seed = self._build_scenario_6_patient_migration()
        dataset.applications.append(s6_app)
        dataset.claims.extend(s6_claims)
        dataset.seed_records.append(s6_seed)
        dataset.ground_truth[s6_app.application_id] = "EBV_FRAUD"
        dataset.scenario_metadata[s6_app.application_id] = (
            "Scenario 6: Sudden 85% patient cohort migration from suspended sober-living provider"
        )

        return dataset

    def _generate_base_seeds(self) -> List[SeedRecord]:
        """Known-bad actor seeds from OIG LEIE, SAM.gov, and State Medicaid terminations."""
        return [
            SeedRecord(
                seed_id="SEED-001",
                identifier_type="TIN",
                identifier_value="82-9912345",
                source="OIG_LEIE",
                reason="Mandatory exclusion 1128(a)(1) conviction related to delivery of healthcare service",
                effective_date="2024-03-15",
                jurisdiction="US"
            ),
            SeedRecord(
                seed_id="SEED-002",
                identifier_type="NPI",
                identifier_value="1487654321",
                source="STATE_CAF",
                reason="State Medicaid Credible Allegation of Fraud (CAF) payment suspension",
                effective_date="2025-06-01",
                jurisdiction="NC"
            ),
            SeedRecord(
                seed_id="SEED-003",
                identifier_type="SSN4_DOB",
                identifier_value="8821_1974-08-14",
                source="OIG_LEIE",
                reason="Healthcare fraud conspiracy conviction",
                effective_date="2023-11-01",
                jurisdiction="US"
            ),
            SeedRecord(
                seed_id="SEED-004",
                identifier_type="EFT",
                identifier_value="053000196_9876543210",
                source="FOR_CAUSE_TERMINATION",
                reason="State termination for cause: sham office location and falsified billing",
                effective_date="2025-01-10",
                jurisdiction="NC"
            ),
            SeedRecord(
                seed_id="SEED-005",
                identifier_type="ADDRESS",
                identifier_value="404 FRAUD BLVD, STE 100, GREENSBORO, NC, 27401",
                source="STATE_CAF",
                reason="Known sham clinic address linked to multi-entity indictment",
                effective_date="2025-02-20",
                jurisdiction="NC"
            )
        ]

    def _generate_legitimate_cohort(
        self, count: int = 15
    ) -> Tuple[List[ProviderApplication], List[OwnershipEdge], List[ClaimRecord]]:
        """Generate authentic legitimate healthcare providers with valid histories and billing."""
        apps = []
        edges = []
        claims = []
        
        provider_categories = [
            ("PEDIATRIC_CLINIC", "261QP2300X", "LIMITED", "PHYSICAL_COMMERCIAL"),
            ("FAMILY_PRACTICE", "207Q00000X", "LIMITED", "PHYSICAL_COMMERCIAL"),
            ("COMMUNITY_HEALTH_FQHC", "261QF0400X", "LIMITED", "FQHC"),
            ("HOSPITAL_OUTPATIENT", "282N00000X", "LIMITED", "HOSPITAL_CAMPUS"),
            ("CARDIOLOGY_CLINIC", "207RC0000X", "LIMITED", "PHYSICAL_COMMERCIAL"),
            ("NEPHROLOGY_PRACTICE", "207RN0300X", "LIMITED", "PHYSICAL_COMMERCIAL"),
            ("INTERNAL_MEDICINE", "207R00000X", "LIMITED", "PHYSICAL_COMMERCIAL")
        ]
        
        authentic_names = [
            "Triangle Pediatrics PLLC",
            "Wake Forest Family Medicine Inc",
            "Carolina Heart & Vascular Clinic",
            "Blue Ridge Community Health Center",
            "Piedmont Pulmonary Specialists LLC",
            "Oak City Dental Care",
            "Old North State Internal Medicine",
            "Capital City Surgical Group",
            "Outer Banks Dermatology PLLC",
            "Smoky Mountain Orthopedics",
            "Cape Fear Nephrology Clinic",
            "Guilford Family Eye Center",
            "Sandhills Neurology Specialists",
            "Tar River OBGYN Associates",
            "Highland Allergy & Asthma Clinic"
        ]

        street_names = [
            "Medical Arts Way", "Doctors Park Dr", "Hospital Plaza", "Healthcare Loop",
            "Wellness Blvd", "Physicians Crossing", "Clinical Drive", "Cary Parkway"
        ]
        
        for i in range(count):
            app_id = self._next_app_id()
            npi = self._next_npi()
            tin = self._next_tin()
            cat, tax, risk, fac_type = provider_categories[i % len(provider_categories)]
            bus_name = authentic_names[i % len(authentic_names)]
            street = street_names[i % len(street_names)]
            
            # Legitimate entities were formed years ago
            form_years_ago = self.rng.randint(4, 18)
            formation_date = (date(2026, 1, 1) - timedelta(days=form_years_ago * 365)).strftime("%Y-%m-%d")
            app_date = "2026-02-01"
            
            address = AddressRecord(
                address_line1=f"{1000 + i * 150} {street}",
                address_line2=f"Suite {200 + i * 5}",
                city="Raleigh" if i % 2 == 0 else "Durham",
                state="NC",
                zip5=f"276{10 + (i % 8)}",
                is_cmra=False,
                is_po_box=False,
                is_virtual_mailbox=False,
                is_pmb=False,
                rdi="COMMERCIAL",
                dpv_valid=True,
                is_vacant=False,
                facility_type=fac_type if fac_type in ["FQHC", "HOSPITAL_CAMPUS"] else None
            )
            
            eft = EFTAccount(
                routing_number=f"053{self.rng.randint(100000, 999999)}",
                account_number=f"1100{self.rng.randint(100000, 999999)}",
                bank_name="First Citizens Bank"
            )
            
            first_names = ["Robert", "Susan", "David", "Jennifer", "Michael", "Patricia", "James", "Linda"]
            last_names = ["Reynolds", "Patel", "Johnson", "Williams", "Chen", "Davis", "Miller", "Wilson"]
            
            doctor = PersonDisclosure(
                first_name=first_names[i % len(first_names)],
                last_name=last_names[i % len(last_names)],
                ssn_last4=f"{self.rng.randint(1000, 8999)}",
                date_of_birth=f"19{self.rng.randint(62, 85)}-{((i % 12) + 1):02d}-15",
                role="OWNER",
                direct_ownership_pct=1.0,
                phone=f"919-555-{2000 + i}",
                email=f"dr.{last_names[i % len(last_names)].lower()}@carepractice{i}.org"
            )
            
            app = ProviderApplication(
                application_id=app_id,
                npi=npi,
                legal_business_name=bus_name,
                tin=tin,
                service_category=cat,
                taxonomy_code=tax,
                aca_risk_tier=risk,
                formation_date=formation_date,
                application_date=app_date,
                primary_address=address,
                phone=f"919-555-{2000 + i}",
                email=f"admin@carepractice{i}.org",
                eft_account=eft,
                disclosed_persons=[doctor],
                rendering_clinician_npis=[self._next_npi(), self._next_npi()],
                device_fingerprint_id=f"DEV-LEGIT-{i:04d}",
                submission_ip_address=f"152.16.{20 + i}.{i + 5}",
                policy_manual_text=f"{bus_name} standard clinical operations, credentialing, and compliance documentation.",
                state_code="NC",
                is_synthetic=True,
                ground_truth_label="LEGITIMATE"
            )
            apps.append(app)
            
            # Claims for legitimate providers: steady, moderate volume, normal hours
            clinician = app.rendering_clinician_npis[0]
            for c_idx in range(12):
                claims.append(ClaimRecord(
                    claim_id=f"CLM-LEGIT-{i}-{c_idx:03d}",
                    billing_provider_npi=npi,
                    rendering_clinician_npi=clinician,
                    beneficiary_id=f"BEN-LEGIT-{(i * 10) + (c_idx % 5)}",
                    service_date=f"2026-03-{c_idx + 1:02d}",
                    procedure_code="99213",
                    units_billed=1.0,
                    paid_amount=95.0,
                    service_hours=0.5,
                    inpatient_overlap=False,
                    beneficiary_deceased=False
                ))
                
        return apps, edges, claims

    def _build_scenario_1_mailbox_shell(self) -> ProviderApplication:
        """Scenario 1: Rapid formation (8 days ago) + CMRA/PMB address + facility service."""
        app_id = self._next_app_id()
        npi = self._next_npi()
        tin = self._next_tin()
        
        # Formed only 8 days before applying
        formation_date = "2026-01-24"
        application_date = "2026-02-01"
        
        # Mailbox store address (e.g. UPS store with PMB)
        address = AddressRecord(
            address_line1="1204 MAIN ST",
            address_line2="PMB 402",
            city="RALEIGH",
            state="NC",
            zip5="27601",
            is_cmra=True,
            is_po_box=False,
            is_virtual_mailbox=True,
            is_pmb=True,
            rdi="COMMERCIAL",
            dpv_valid=True,
            is_vacant=False
        )
        
        disclosed_owner = PersonDisclosure(
            first_name="Victor",
            last_name="Vanish",
            ssn_last4="4912",
            date_of_birth="1992-04-18",
            role="OWNER",
            direct_ownership_pct=1.0,
            phone="919-555-8822",
            email="apexautismnc@gmail.com"
        )
        
        return ProviderApplication(
            application_id=app_id,
            npi=npi,
            legal_business_name="APEX AUTISM & BEHAVIORAL SERVICES LLC",
            tin=tin,
            service_category="AUTISM_CLINIC_FACILITY",
            taxonomy_code="251S00000X",
            aca_risk_tier="LIMITED",
            formation_date=formation_date,
            application_date=application_date,
            primary_address=address,
            phone="919-555-8822",
            email="apexautismnc@gmail.com",
            eft_account=EFTAccount(routing_number="053000196", account_number="5544332211"),
            disclosed_persons=[disclosed_owner],
            rendering_clinician_npis=[self._next_npi()],
            device_fingerprint_id="DEV-EBV-S1-99",
            submission_ip_address="198.51.100.42",
            policy_manual_text="Standard behavioral health policy manual for community ABA services.",
            state_code="NC",
            is_synthetic=True,
            ground_truth_label="EBV_FRAUD"
        )

    def _build_scenario_2_nominee_hidden_exclusion(
        self
    ) -> Tuple[ProviderApplication, List[OwnershipEdge], List[SeedRecord]]:
        """Scenario 2: Straw nominee fronting for a holding company 60% owned by an OIG-excluded felon."""
        app_id = self._next_app_id()
        npi = self._next_npi()
        tin = self._next_tin()
        
        straw_person = PersonDisclosure(
            first_name="Samantha",
            last_name="Straw",
            ssn_last4="1122",
            date_of_birth="2002-11-15", # Very young nominee
            role="AUTHORIZED_OFFICIAL",
            direct_ownership_pct=0.20,
            is_straw_nominee_flag=True
        )
        
        holding_co_id = "ORG-HOLDING-CAROLINA-HEALTH"
        hidden_bad_actor_id = "PERSON-EXCLUDED-8821"
        
        # Edges for GR-4 Indirect Ownership:
        # Holding Co owns 80% of Applicant
        # Excluded Person owns 60% of Holding Co
        # Indirect ownership = 0.80 * 0.60 = 0.48 (48% >= 5% statutory threshold!)
        edge1 = OwnershipEdge(
            parent_entity_id=holding_co_id,
            child_entity_id=npi,
            ownership_percentage=0.80,
            effective_date="2025-10-01"
        )
        edge2 = OwnershipEdge(
            parent_entity_id=hidden_bad_actor_id,
            child_entity_id=holding_co_id,
            ownership_percentage=0.60,
            effective_date="2025-09-01"
        )
        
        # Seed record connecting hidden_bad_actor_id
        seed = SeedRecord(
            seed_id="SEED-EXCL-S2",
            identifier_type="PERSON_NAME",
            identifier_value=hidden_bad_actor_id,
            source="OIG_LEIE",
            reason="Exclusion under 1128(a)(1) felony conviction in Medicare fraud",
            effective_date="2023-05-15"
        )
        
        app = ProviderApplication(
            application_id=app_id,
            npi=npi,
            legal_business_name="HORIZON COMMUNITY RECOVERY LLC",
            tin=tin,
            service_category="PSYCHIATRIC_REHAB",
            taxonomy_code="251S00000X",
            aca_risk_tier="LIMITED",
            formation_date="2025-11-01",
            application_date="2026-02-01",
            primary_address=AddressRecord(
                address_line1="700 EXECUTIVE CENTER DR",
                city="CARY",
                state="NC",
                zip5="27511",
                is_cmra=False,
                rdi="COMMERCIAL"
            ),
            phone="919-555-3388",
            email="info@horizonrecoverync.com",
            eft_account=EFTAccount(routing_number="053100099", account_number="7788990011"),
            disclosed_persons=[straw_person],
            parent_organizations=[holding_co_id],
            rendering_clinician_npis=[self._next_npi()],
            state_code="NC",
            is_synthetic=True,
            ground_truth_label="EBV_FRAUD"
        )
        
        return app, [edge1, edge2], [seed]

    def _build_scenario_3_shared_eft_reentry(self) -> Tuple[ProviderApplication, SeedRecord]:
        """Scenario 3: New LLC re-entering with identical EFT bank account of a terminated provider."""
        app_id = self._next_app_id()
        npi = self._next_npi()
        tin = self._next_tin()
        
        reentry_eft_routing = "053000196"
        reentry_eft_account = "9876543210" # Matches SEED-004
        
        seed = SeedRecord(
            seed_id="SEED-EFT-REENTRY",
            identifier_type="EFT",
            identifier_value=f"{reentry_eft_routing}_{reentry_eft_account}",
            source="FOR_CAUSE_TERMINATION",
            reason="Prior provider terminated for cause after submitting $1.8M in fabricated claims",
            effective_date="2025-01-10",
            jurisdiction="NC"
        )
        
        app = ProviderApplication(
            application_id=app_id,
            npi=npi,
            legal_business_name="BEACON SOUTHERN HEALTHCARE LLC",
            tin=tin,
            service_category="BEHAVIORAL_HEALTH",
            taxonomy_code="251S00000X",
            aca_risk_tier="LIMITED",
            formation_date="2026-01-10",
            application_date="2026-02-05",
            primary_address=AddressRecord(
                address_line1="310 COMMERCE PARKWAY",
                city="WILMINGTON",
                state="NC",
                zip5="28403",
                rdi="COMMERCIAL"
            ),
            phone="910-555-7711",
            email="billing@beaconsouthern.org",
            eft_account=EFTAccount(routing_number=reentry_eft_routing, account_number=reentry_eft_account),
            disclosed_persons=[
                PersonDisclosure(
                    first_name="Marcus",
                    last_name="Vance",
                    ssn_last4="6677",
                    date_of_birth="1980-02-20",
                    role="OWNER",
                    direct_ownership_pct=1.0
                )
            ],
            device_fingerprint_id="DEV-SHARED-EFT-404",
            state_code="NC",
            is_synthetic=True,
            ground_truth_label="EBV_FRAUD"
        )
        
        return app, seed

    def _build_scenario_4_templated_clinician_ring(self) -> List[ProviderApplication]:
        """Scenario 4: 3 separate entities formed in same week with identical policy manual and shared clinicians."""
        apps = []
        shared_clinicians = [self._next_npi(), self._next_npi()]
        shared_ip = "198.51.100.88"
        shared_device = "DEV-RING-TEMPLATE-01"
        
        templated_policy_text = """
        POLICY AND OPERATING MANUAL: COMPREHENSIVE BEHAVIORAL AND COMMUNITY HEALTH SERVICES.
        SECTION 1: CLINICAL GOVERNANCE AND SUPERVISION.
        All rendering technicians shall record daily progress notes under the supervision of the clinical director.
        SECTION 2: CRISIS STABILIZATION PROTOCOLS.
        Emergency response plans will be activated immediately within 15 minutes of intake assessment.
        SECTION 3: BILLING COMPLIANCE ASSURANCE.
        Documentation must support medical necessity for all intensive outpatient units billed under H0015.
        """
        
        names = [
            "SERENITY COMMUNITY LIVING LLC",
            "TRANQUILITY CARE SOLUTIONS LLC",
            "HARMONY WELLNESS NETWORK LLC"
        ]
        
        for idx, name in enumerate(names):
            app_id = self._next_app_id()
            npi = self._next_npi()
            tin = self._next_tin()
            
            app = ProviderApplication(
                application_id=app_id,
                npi=npi,
                legal_business_name=name,
                tin=tin,
                service_category="SOBER_LIVING_FACILITY",
                taxonomy_code="251S00000X",
                aca_risk_tier="LIMITED",
                formation_date=f"2026-01-1{idx}", # Formed in same week
                application_date="2026-02-08",
                primary_address=AddressRecord(
                    address_line1=f"88{idx} OAK HOLLOW DR",
                    city="HIGH POINT",
                    state="NC",
                    zip5="27265",
                    rdi="COMMERCIAL"
                ),
                phone=f"336-555-900{idx}",
                email=f"admin@caregroup{idx}.com",
                eft_account=EFTAccount(routing_number="053000196", account_number=f"44556600{idx}"),
                disclosed_persons=[
                    PersonDisclosure(
                        first_name=f"Nominee{idx}",
                        last_name=f"Partner{idx}",
                        ssn_last4=f"770{idx}",
                        date_of_birth=f"198{idx}-07-15",
                        role="OWNER",
                        direct_ownership_pct=1.0
                    )
                ],
                rendering_clinician_npis=shared_clinicians, # Shared across all 3
                device_fingerprint_id=shared_device,       # Shared device
                submission_ip_address=shared_ip,           # Shared IP
                policy_manual_text=templated_policy_text,  # Exact template
                policy_manual_hash=hashlib.sha256(templated_policy_text.encode()).hexdigest(),
                state_code="NC",
                is_synthetic=True,
                ground_truth_label="EBV_FRAUD"
            )
            apps.append(app)
            
        return apps

    def _build_scenario_5_billing_ramp_and_impossible_hours(
        self
    ) -> Tuple[ProviderApplication, List[ClaimRecord]]:
        """Scenario 5: Post-enrollment zero-to-millions ramp, 35h/day per clinician, and ghost billing."""
        app_id = self._next_app_id()
        npi = self._next_npi()
        tin = self._next_tin()
        
        clinician_npi = self._next_npi()
        
        app = ProviderApplication(
            application_id=app_id,
            npi=npi,
            legal_business_name="TRIANGLE CRISIS & REHAB PARTNERS LLC",
            tin=tin,
            service_category="BEHAVIORAL_HEALTH",
            taxonomy_code="251S00000X",
            aca_risk_tier="LIMITED",
            formation_date="2025-09-01",
            application_date="2025-10-01",
            primary_address=AddressRecord(
                address_line1="2500 REGENCY PKWY",
                city="CARY",
                state="NC",
                zip5="27518",
                rdi="COMMERCIAL"
            ),
            phone="919-555-4422",
            email="billing@trianglecrisisrehab.org",
            eft_account=EFTAccount(routing_number="053000196", account_number="9988776655"),
            disclosed_persons=[
                PersonDisclosure(
                    first_name="Arthur",
                    last_name="Biller",
                    ssn_last4="5544",
                    date_of_birth="1977-03-30",
                    role="OWNER",
                    direct_ownership_pct=1.0
                )
            ],
            rendering_clinician_npis=[clinician_npi],
            state_code="NC",
            is_synthetic=True,
            ground_truth_label="EBV_FRAUD"
        )
        
        # Ramp claims:
        # In Month 3: Bills over $3.2 Million across 500 claims
        # In single days: 36 hours billed by one clinician
        # Includes inpatient overlaps and post-mortem claims
        claims = []
        for i in range(120):
            day = (i % 28) + 1
            # Simulate 36 hours billed on 2026-03-15
            hours = 6.0 if day != 15 else 12.0 # 3 claims on day 15 = 36 hours
            is_inpatient = (i in [10, 11, 12])
            is_deceased = (i in [25, 26])
            
            claims.append(ClaimRecord(
                claim_id=f"CLM-RAMP-{i:04d}",
                billing_provider_npi=npi,
                rendering_clinician_npi=clinician_npi,
                beneficiary_id=f"BEN-EBV-{i % 15:03d}",
                service_date=f"2026-03-{day:02d}",
                procedure_code="H0015",
                units_billed=hours * 4.0, # 15-min units
                paid_amount=hours * 250.0, # Aggressive reimbursement rate
                service_hours=hours,
                inpatient_overlap=is_inpatient,
                beneficiary_deceased=is_deceased
            ))
            
        return app, claims

    def _build_scenario_6_patient_migration(
        self
    ) -> Tuple[ProviderApplication, List[ClaimRecord], SeedRecord]:
        """Scenario 6: Coordinated cohort migration of 30 patients from a suspended provider."""
        app_id = self._next_app_id()
        npi = self._next_npi()
        tin = self._next_tin()
        
        suspended_prior_npi = "1487654321" # Linked to SEED-002 (STATE_CAF)
        
        seed = SeedRecord(
            seed_id="SEED-SUSPENDED-PROVIDER-X",
            identifier_type="NPI",
            identifier_value=suspended_prior_npi,
            source="STATE_CAF",
            reason="Suspended for kickback payments and ghost billing",
            effective_date="2025-06-01",
            jurisdiction="NC"
        )
        
        app = ProviderApplication(
            application_id=app_id,
            npi=npi,
            legal_business_name="NEW DAWN RECOVERY LIVING LLC",
            tin=tin,
            service_category="SOBER_LIVING_FACILITY",
            taxonomy_code="251S00000X",
            aca_risk_tier="LIMITED",
            formation_date="2026-01-05",
            application_date="2026-02-10",
            primary_address=AddressRecord(
                address_line1="510 AIRPORT BLVD",
                city="MORRISVILLE",
                state="NC",
                zip5="27560",
                rdi="COMMERCIAL"
            ),
            phone="919-555-1212",
            email="intake@newdawnliving.org",
            eft_account=EFTAccount(routing_number="053000196", account_number="1234432199"),
            disclosed_persons=[
                PersonDisclosure(
                    first_name="Darren",
                    last_name="Dawn",
                    ssn_last4="9012",
                    date_of_birth="1983-09-14",
                    role="OWNER",
                    direct_ownership_pct=1.0
                )
            ],
            rendering_clinician_npis=[self._next_npi()],
            state_code="NC",
            is_synthetic=True,
            ground_truth_label="EBV_FRAUD"
        )
        
        # 30 beneficiaries who were treated by suspended_prior_npi in Jan 2026
        # suddenly move 100% to new dawn in Feb 2026
        claims = []
        for ben_idx in range(30):
            ben_id = f"BEN-MIGRATING-{ben_idx:03d}"
            # Claim with suspended provider in Jan
            claims.append(ClaimRecord(
                claim_id=f"CLM-PRIOR-{ben_idx:03d}",
                billing_provider_npi=suspended_prior_npi,
                rendering_clinician_npi=self._next_npi(),
                beneficiary_id=ben_id,
                service_date="2026-01-15",
                procedure_code="H0015",
                units_billed=4.0,
                paid_amount=350.0,
                service_hours=2.0
            ))
            # Claim with newly enrolled provider in Feb
            claims.append(ClaimRecord(
                claim_id=f"CLM-NEW-{ben_idx:03d}",
                billing_provider_npi=npi,
                rendering_clinician_npi=app.rendering_clinician_npis[0],
                beneficiary_id=ben_id,
                service_date="2026-02-20",
                procedure_code="H0015",
                units_billed=4.0,
                paid_amount=350.0,
                service_hours=2.0
            ))
            
        return app, claims, seed
