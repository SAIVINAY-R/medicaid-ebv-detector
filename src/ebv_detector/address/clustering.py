"""M2 Address Co-Location Clustering with Allowlist Suppression.

Identifies high-density address co-location (e.g., 5+ unrelated provider entities at same suite),
while exempting legitimate multi-provider healthcare institutions via an allowlist
(FQHCs, Community Health Centers, Academic Medical Centers, Tribal Health Centers).
"""

from collections import defaultdict
from typing import Dict, List, Optional, Set

from ..config import AddressConfig
from ..models.entities import AddressRecord, ProviderApplication
from ..models.results import EvidenceItem, EvidenceSeverity


class AddressClusterEngine:
    """Monitors co-location density across applications."""

    def __init__(self, config: Optional[AddressConfig] = None):
        self.config = config or AddressConfig()
        # normalized_address -> list of provider_npis
        self._address_registry: Dict[str, List[str]] = defaultdict(list)
        # npi -> app details
        self._provider_meta: Dict[str, ProviderApplication] = {}

    def register_provider(self, app: ProviderApplication):
        """Add an application's primary location to the registry."""
        if not app.primary_address:
            return
        addr_key = app.primary_address.normalized_single_line
        if app.npi not in self._address_registry[addr_key]:
            self._address_registry[addr_key].append(app.npi)
        self._provider_meta[app.npi] = app

    def evaluate_density(self, app: ProviderApplication) -> List[EvidenceItem]:
        """Check if application location exceeds unrelated provider density threshold."""
        if not app.primary_address:
            return []

        addr = app.primary_address
        addr_key = addr.normalized_single_line
        
        # 1. Check Allowlist: FQHC, CHC, Hospital Campus, etc.
        fac_type = (addr.facility_type or "").upper()
        if fac_type in self.config.allowlisted_facility_types:
            return [EvidenceItem(
                code="M2-ADDR-ALLOWLIST-EXEMPT",
                description=f"Address recognized as accredited multi-provider facility ({fac_type}); density check exempt.",
                source_layer="M2_ADDRESS",
                severity=EvidenceSeverity.INFO,
                verified_fact=True,
                details={"facility_type": fac_type, "address": addr_key}
            )]

        # 2. Count providers at this exact normalized address
        co_located_npis = self._address_registry.get(addr_key, [])
        # Exclude self if already registered
        other_npis = [npi for npi in co_located_npis if npi != app.npi]
        density_count = len(other_npis) + 1

        if density_count >= self.config.unrelated_provider_density_threshold:
            return [EvidenceItem(
                code="M2-ADDR-HIGH-DENSITY-CLUSTER",
                description=(
                    f"Address has {density_count} provider entities registered (threshold is "
                    f"{self.config.unrelated_provider_density_threshold}). Potential virtual office or shell cluster."
                ),
                source_layer="M2_ADDRESS",
                severity=EvidenceSeverity.HIGH,
                verified_fact=True,
                details={
                    "address": addr_key,
                    "provider_count": density_count,
                    "co_located_npis": other_npis
                }
            )]

        return []
