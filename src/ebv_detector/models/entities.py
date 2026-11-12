"""Data models representing providers, individuals, addresses, claims, and seed records.

Designed to mirror Dataverse and CMS provider-enrollment disclosure models
under 42 CFR 455.104-106 and 42 CFR 455.450.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Set


@dataclass
class AddressRecord:
    """Address entity with USPS / Melissa CASS/DPV/CMRA hygiene attributes."""
    address_line1: str
    address_line2: Optional[str] = None
    city: str = ""
    state: str = ""
    zip5: str = ""
    zip4: Optional[str] = None
    
    # Melissa / USPS Attributes
    is_cmra: bool = False             # Commercial Mail Receiving Agency (e.g. UPS Store)
    is_po_box: bool = False           # PO Box
    is_virtual_mailbox: bool = False  # Virtual office / digital mailbox service
    is_pmb: bool = False              # Private Mailbox
    rdi: str = "COMMERCIAL"           # "RESIDENTIAL" or "COMMERCIAL"
    dpv_valid: bool = True            # Delivery Point Validation confirmed
    is_vacant: bool = False           # USPS reports vacant
    
    # Geocodes
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    
    # Campus or facility override (e.g. FQHC, Hospital Campus)
    facility_type: Optional[str] = None

    @property
    def normalized_single_line(self) -> str:
        """Standardized single line string for blocking and comparison."""
        parts = [self.address_line1.strip().upper()]
        if self.address_line2:
            parts.append(self.address_line2.strip().upper())
        parts.extend([self.city.strip().upper(), self.state.strip().upper(), self.zip5.strip()])
        return ", ".join(parts)

    @property
    def is_prohibited_by_data_039(self) -> bool:
        """Check if address matches DATA-039 / DT-SCR-GATE-002 prohibited types."""
        return self.is_cmra or self.is_po_box or self.is_virtual_mailbox or self.is_pmb


@dataclass
class PersonDisclosure:
    """Individual disclosed in provider application under 42 CFR 455.104-106."""
    first_name: str
    last_name: str
    ssn_last4: str
    date_of_birth: str                # YYYY-MM-DD
    role: str                         # "OWNER", "MANAGING_EMPLOYEE", "AUTHORIZED_OFFICIAL", "DIRECTOR"
    direct_ownership_pct: float = 0.0 # 0.0 to 1.0 (e.g. 0.25 = 25%)
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[AddressRecord] = None
    has_criminal_record_disclosed: bool = False
    is_straw_nominee_flag: bool = False  # Used in synthetic simulation

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip().upper()

    @property
    def ssn_dob_key(self) -> str:
        return f"{self.ssn_last4}_{self.date_of_birth}"


@dataclass
class EFTAccount:
    """Bank routing and account information for electronic funds transfer."""
    routing_number: str
    account_number: str
    bank_name: str = ""
    effective_start_date: Optional[str] = None
    effective_end_date: Optional[str] = None

    @property
    def account_key(self) -> str:
        return f"{self.routing_number}_{self.account_number}"


@dataclass
class OwnershipEdge:
    """Direct or parent holding company ownership link (feeds GR-4 indirect calculation)."""
    parent_entity_id: str             # Parent Person ID or Holding Org ID
    child_entity_id: str              # Subsidiary Org ID
    ownership_percentage: float       # 0.0 to 1.0
    effective_date: str = "2020-01-01"
    end_date: Optional[str] = None


@dataclass
class ProviderApplication:
    """Full Medicaid provider enrollment application."""
    application_id: str
    npi: str
    legal_business_name: str
    tin: str
    dba_name: Optional[str] = None
    
    # Categorical classification
    service_category: str = "BEHAVIORAL_HEALTH"  # HCBS, ADULT_DAY, AUTISM_ABA, NEMT, HOSPICE, etc.
    taxonomy_code: str = "251S00000X"            # Community/Behavioral Health
    aca_risk_tier: str = "LIMITED"               # "LIMITED", "MODERATE", "HIGH" (Layer 1 sets this)
    
    # Dates
    formation_date: str = "2026-01-01"           # Secretary of State formation
    application_date: str = "2026-02-01"         # Date submitted
    
    # Contact & Identifiers
    primary_address: Optional[AddressRecord] = None
    practice_locations: List[AddressRecord] = field(default_factory=list)
    phone: str = ""
    email: str = ""
    eft_account: Optional[EFTAccount] = None
    
    # Disclosed Individuals & Entities
    disclosed_persons: List[PersonDisclosure] = field(default_factory=list)
    parent_organizations: List[str] = field(default_factory=list)
    rendering_clinician_npis: List[str] = field(default_factory=list)
    supervising_clinician_npis: List[str] = field(default_factory=list)
    
    # Telemetry & Documentation
    device_fingerprint_id: Optional[str] = None
    submission_ip_address: Optional[str] = None
    policy_manual_text: Optional[str] = None
    policy_manual_hash: Optional[str] = None
    
    # Screening flags
    prior_termination_disclosed: bool = False
    is_change_of_ownership: bool = False
    prior_enrolled_npi: Optional[str] = None
    
    # Metadata
    state_code: str = "NC"                       # NC, ND, NH, MN, etc.
    is_synthetic: bool = False
    ground_truth_label: Optional[str] = None     # "EBV_FRAUD", "LEGITIMATE", for benchmarking only

    @property
    def days_since_formation(self) -> int:
        """Calculate days between Secretary of State entity formation and application."""
        try:
            d_form = datetime.strptime(self.formation_date, "%Y-%m-%d").date()
            d_app = datetime.strptime(self.application_date, "%Y-%m-%d").date()
            return max(0, (d_app - d_form).days)
        except Exception:
            return 999


@dataclass
class SeedRecord:
    """Known-bad actor seed record (LEIE, SAM, state exclusions, prior CAF suspensions)."""
    seed_id: str
    identifier_type: str              # "NPI", "TIN", "SSN4_DOB", "EFT", "ADDRESS", "PHONE", "PERSON_NAME"
    identifier_value: str
    source: str                       # "OIG_LEIE", "SAM_GOV", "STATE_CAF", "FOR_CAUSE_TERMINATION", "CMS_REVOCATION"
    reason: str                       # Reason for sanction / exclusion
    effective_date: str
    end_date: Optional[str] = None
    jurisdiction: str = "US"          # "US" or state code


@dataclass
class ClaimRecord:
    """Medicaid MMIS claim record for Checkpoint 2 early-billing analysis."""
    claim_id: str
    billing_provider_npi: str
    rendering_clinician_npi: str
    beneficiary_id: str
    service_date: str                 # YYYY-MM-DD
    procedure_code: str
    units_billed: float
    paid_amount: float
    service_hours: float
    inpatient_overlap: bool = False   # True if beneficiary was an inpatient in hospital
    beneficiary_deceased: bool = False # True if service date is post-mortem

