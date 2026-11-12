"""M2 Address Risk & DATA-039 Prohibited Address Type Rules.

Evaluates USPS and Melissa CASS/DPV/CMRA hygiene indicators against Medicaid policy:
- DATA-039 / DT-SCR-GATE-002: Prohibits CMRA, PO Box, Virtual Mailbox, and PMB for facility providers
- Facility vs. Residential Check: Facility-based services (Adult Day, Psych Rehab, Sober Living, ABA Clinic)
  cannot legitimately operate from residential delivery locations (RDI = RESIDENTIAL)
- Vacancy Flag: USPS-reported vacant addresses trigger verification
"""

from typing import List, Optional, Tuple

from ..config import AddressConfig
from ..models.entities import AddressRecord
from ..models.results import EvidenceItem, EvidenceSeverity


def evaluate_address_rules(
    address: Optional[AddressRecord],
    service_category: str,
    config: Optional[AddressConfig] = None
) -> Tuple[List[EvidenceItem], bool]:
    """Evaluate address hygiene, Melissa indicators, and DATA-039 prohibitions.
    
    Returns:
        (evidence_items, is_fatal_prohibition)
    """
    if not address:
        item = EvidenceItem(
            code="L1-ADDR-MISSING",
            description="Primary practice location address is missing from application disclosures.",
            source_layer="LAYER_1",
            severity=EvidenceSeverity.CRITICAL,
            verified_fact=True,
            details={"service_category": service_category}
        )
        return [item], True

    cfg = config or AddressConfig()
    evidence: List[EvidenceItem] = []
    is_fatal = False

    # 1. Prohibited Address Types under DATA-039 / DT-SCR-GATE-002
    if address.is_cmra:
        evidence.append(EvidenceItem(
            code="L1-ADDR-CMRA-PROHIBITED",
            description="Address is a Commercial Mail Receiving Agency (CMRA / Mailbox Store), prohibited under DATA-039.",
            source_layer="LAYER_1",
            severity=EvidenceSeverity.CRITICAL,
            verified_fact=True,
            details={"address": address.normalized_single_line}
        ))
        is_fatal = True

    if address.is_po_box:
        evidence.append(EvidenceItem(
            code="L1-ADDR-POBOX-PROHIBITED",
            description="Address is a PO Box, prohibited as a primary service location under 42 CFR 455.416 and DATA-039.",
            source_layer="LAYER_1",
            severity=EvidenceSeverity.CRITICAL,
            verified_fact=True,
            details={"address": address.normalized_single_line}
        ))
        is_fatal = True

    if address.is_virtual_mailbox or address.is_pmb:
        evidence.append(EvidenceItem(
            code="L1-ADDR-VIRTUAL-PROHIBITED",
            description="Address is identified as a Virtual Mailbox or Private Mailbox (PMB).",
            source_layer="LAYER_1",
            severity=EvidenceSeverity.HIGH,
            verified_fact=True,
            details={"address": address.normalized_single_line}
        ))
        is_fatal = True

    # 2. Facility Service at Residential Address
    is_facility = service_category.upper() in cfg.facility_service_types
    if is_facility and address.rdi.upper() == "RESIDENTIAL":
        evidence.append(EvidenceItem(
            code="L1-ADDR-FACILITY-RESIDENTIAL-MISMATCH",
            description=(
                f"Facility-based service category '{service_category}' declared at a residential address (RDI=RESIDENTIAL). "
                "Unannounced site visit required."
            ),
            source_layer="LAYER_1",
            severity=EvidenceSeverity.CRITICAL,
            verified_fact=True,
            details={"service_category": service_category, "rdi": address.rdi}
        ))
        is_fatal = True

    # 3. USPS Vacancy
    if address.is_vacant:
        evidence.append(EvidenceItem(
            code="M2-ADDR-USPS-VACANT",
            description="USPS delivery records report the location as currently vacant.",
            source_layer="M2_ADDRESS",
            severity=EvidenceSeverity.HIGH,
            verified_fact=True,
            details={"address": address.normalized_single_line}
        ))

    # 4. Delivery Point Validation (DPV) Failure
    if not address.dpv_valid:
        evidence.append(EvidenceItem(
            code="M2-ADDR-DPV-INVALID",
            description="Address failed USPS Delivery Point Validation (DPV confirmed = False).",
            source_layer="M2_ADDRESS",
            severity=EvidenceSeverity.MEDIUM,
            verified_fact=True,
            details={"address": address.normalized_single_line}
        ))

    return evidence, is_fatal

