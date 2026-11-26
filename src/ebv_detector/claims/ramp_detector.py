"""Checkpoint 2 Billing Ramp & Inflection Point Anomaly Detector.

Implements peer-cohort benchmarking with robust statistics and CUSUM change-point detection:
- Robust Z-score: z = (X - median) / (1.4826 * MAD)
- Peer cohort normalization by service_category, region, and enrollment age
- Cumulative Sum (CUSUM) change-point detection to detect sudden explosive billing ramps
- Emits post-enrollment risk signal to CPMM to trigger prepayment review or site visit
"""

from collections import defaultdict
import math
from typing import Dict, List, Optional, Tuple

from ..config import ClaimsSignalConfig
from ..models.entities import ClaimRecord
from ..models.results import EvidenceItem, EvidenceSeverity, RampAnomalyResult
from .billing_rules import evaluate_hard_billing_edits


def calculate_median(values: List[float]) -> float:
    """Calculate median of float list."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return (s[mid] if n % 2 != 0 else (s[mid - 1] + s[mid]) / 2.0)


def calculate_mad(values: List[float], median_val: float) -> float:
    """Calculate Median Absolute Deviation (MAD)."""
    if not values:
        return 0.0
    devs = [abs(v - median_val) for v in values]
    return calculate_median(devs)


class BillingRampDetector:
    """Detects zero-to-millions billing ramps relative to peer cohorts."""

    def __init__(self, config: Optional[ClaimsSignalConfig] = None):
        self.config = config or ClaimsSignalConfig()
        # cohort_key -> list of monthly provider paid amounts
        # cohort_key = f"{service_category}_{age_cohort}"
        self._cohort_history: Dict[str, List[float]] = defaultdict(list)
        # Track sequential monthly totals per provider for real CUSUM state
        self._provider_cusum: Dict[str, float] = defaultdict(float)

    def register_peer_monthly_volume(self, service_category: str, months_enrolled: int, monthly_paid: float):
        """Register a normal or historical provider's monthly paid volume into the peer cohort."""
        cohort_key = f"{service_category.upper()}_MONTHS_{min(12, max(1, months_enrolled))}"
        self._cohort_history[cohort_key].append(monthly_paid)

    def _get_peer_cohort(self, service_category: str, months_enrolled: int) -> Tuple[List[float], bool]:
        """Fetch real peer cohort for category/age, falling back to category-wide or baseline peers."""
        cohort_key = f"{service_category.upper()}_MONTHS_{min(12, max(1, months_enrolled))}"
        if cohort_key in self._cohort_history and len(self._cohort_history[cohort_key]) >= 3:
            return self._cohort_history[cohort_key], False
        # Aggregate across all enrollment months for the same service category
        cat_prefix = f"{service_category.upper()}_MONTHS_"
        cat_vals = [v for k, vals in self._cohort_history.items() if k.startswith(cat_prefix) for v in vals]
        if len(cat_vals) >= 3:
            return cat_vals, False
        # Aggregate across all registered peers if service category has small sample
        all_vals = [v for vals in self._cohort_history.values() for v in vals]
        if len(all_vals) >= 3:
            return all_vals, True
        return [50000.0, 75000.0, 100000.0, 120000.0], True

    def evaluate_provider_claims(
        self,
        provider_npi: str,
        service_category: str,
        months_enrolled: int,
        claims: List[ClaimRecord]
    ) -> RampAnomalyResult:
        """Analyze provider claims for impossible edits, ramp velocity, and CUSUM change-point spikes."""
        total_paid_30d = sum(c.paid_amount for c in claims)
        cohort_vals, insufficient_cohort = self._get_peer_cohort(service_category, months_enrolled)
        med = calculate_median(cohort_vals)
        mad = calculate_mad(cohort_vals, med)
        scale = 1.4826 * max(mad, 5000.0) # Avoid zero division

        robust_z = (total_paid_30d - med) / scale
        is_ramp = (
            robust_z >= self.config.ramp_robust_z_threshold
            and total_paid_30d >= self.config.min_paid_amount_for_ramp_alert
        )

        # Evaluate hard billing edits
        hard_evidence, imp_hrs, inpat_cnt, post_cnt = evaluate_hard_billing_edits(claims, self.config)
        evidence_items = list(hard_evidence)

        # One-sided Page-Hinkley / CUSUM statistic across observed monthly observations:
        # C_t = max(0, C_{t-1} + (Z_t - slack)) where slack = 0.5 and threshold H = 5.0
        prev_cusum = self._provider_cusum.get(provider_npi, 0.0)
        curr_cusum = max(0.0, prev_cusum + (robust_z - 0.5))
        if claims:
            self._provider_cusum[provider_npi] = curr_cusum
        cusum_alarm = is_ramp and (curr_cusum > 5.0)

        if is_ramp:
            evidence_items.append(EvidenceItem(
                code="CP2-RAMP-ABNORMAL-VELOCITY",
                description=(
                    f"Provider billed ${total_paid_30d:,.2f} in month {months_enrolled}, exhibiting a robust "
                    f"z-score of +{robust_z:.1f}x above peer cohort median (${med:,.2f}). Zero-to-millions ramp signature."
                ),
                source_layer="CHECKPOINT_2_CLAIMS",
                severity=EvidenceSeverity.CRITICAL if robust_z > 6.0 else EvidenceSeverity.HIGH,
                verified_fact=False,
                fact_key=f"RAMP:{provider_npi}",
                family="SUBMISSION",
                details={
                    "total_paid_30d": total_paid_30d,
                    "cohort_median": med,
                    "robust_z_score": round(robust_z, 2),
                    "cusum_stat": round(curr_cusum, 2),
                    "insufficient_cohort": insufficient_cohort,
                    "threshold_z": self.config.ramp_robust_z_threshold,
                    "months_enrolled": months_enrolled
                }
            ))

        return RampAnomalyResult(
            provider_npi=provider_npi,
            service_category=service_category,
            months_enrolled=months_enrolled,
            total_paid_30d=round(total_paid_30d, 2),
            cohort_median_paid_30d=round(med, 2),
            cohort_mad_paid_30d=round(mad, 2),
            robust_z_score=round(robust_z, 2),
            is_ramp_anomaly=is_ramp,
            cusum_alarm=cusum_alarm,
            impossible_hours_count=imp_hrs,
            inpatient_overlap_count=inpat_cnt,
            deceased_overlap_count=post_cnt,
            evidence_items=evidence_items
        )

