"""Synthetic EBV data generator and scenario synthesizer.

Built strictly from public statistics, federal indictments (DOJ 2026 takedowns),
and domain rules without using proprietary State Data (NC RFP Attachment C §3(e)(ii) compliant).
"""

from .generator import SyntheticEBVGenerator, SyntheticDataset

__all__ = ["SyntheticEBVGenerator", "SyntheticDataset"]

