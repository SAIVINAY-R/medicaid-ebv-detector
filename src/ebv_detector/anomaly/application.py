"""M4 Application Telemetry and Velocity Anomaly Detector.

Detects coordinated submission rings based on:
- High submission velocity from same IP address within 7 days
- Multiple distinct provider applications from same physical device fingerprint
- Rapid entity formation (formed < 30 days prior to application)
- Re-use of email domain or phone across distinct provider groups
"""

from collections import defaultdict
from datetime import datetime, timedelta
import re
from typing import Dict, List, Optional, Tuple

from ..config import ApplicationAnomalyConfig
from ..models.entities import ProviderApplication
from ..models.results import EvidenceItem, EvidenceSeverity


class ApplicationVelocityTracker:
    """Tracks submission velocity and device/IP reuse across applications."""

    def __init__(self, config: Optional[ApplicationAnomalyConfig] = None):
        self.config = config or ApplicationAnomalyConfig()
        # key -> list of (app_id, submission_date_str, clean_tin)
        self._ip_history: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        self._device_history: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        self._email_history: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)

    def _is_within_days(self, current_date_str: str, prior_date_str: str, max_days: int) -> bool:
        """Check if prior date is within max_days of current date."""
        try:
            curr_dt = datetime.strptime(current_date_str, "%Y-%m-%d").date()
            prior_dt = datetime.strptime(prior_date_str, "%Y-%m-%d").date()
            return abs((curr_dt - prior_dt).days) <= max_days
        except Exception:
            return False

    def _record_entry(
        self,
        history: Dict[str, List[Tuple[str, str, str]]],
        key: str,
        app_id: str,
        app_date: str,
        clean_tin: str
    ) -> None:
        """Upsert history record by application_id so re-scoring never creates duplicate counts."""
        history[key] = [entry for entry in history[key] if entry[0] != app_id]
        history[key].append((app_id, app_date, clean_tin))

    def _count_unrelated_within_window(
        self,
        entries: List[Tuple[str, str, str]],
        app_id: str,
        app_date: str,
        clean_tin: str,
        max_days: int
    ) -> List[str]:
        """Count distinct prior applications within the date window that do not share the same TIN."""
        seen_ids: List[str] = []
        for aid, dt, prior_tin in entries:
            if aid == app_id:
                continue
            if clean_tin and prior_tin and clean_tin == prior_tin:
                continue
            if self._is_within_days(app_date, dt, max_days) and aid not in seen_ids:
                seen_ids.append(aid)
        return seen_ids

    def record_and_evaluate(self, app: ProviderApplication, record: bool = True) -> List[EvidenceItem]:
        """Record current application telemetry and check for velocity/clustering anomalies within sliding windows."""
        evidence: List[EvidenceItem] = []
        app_date = app.application_date
        clean_tin = re.sub(r"\D", "", app.tin or "")

        # 1. Formation Age Evaluation (exempt if Change of Ownership)
        if not app.is_change_of_ownership:
            days = app.days_since_formation
            if days is None:
                evidence.append(EvidenceItem(
                    code="M4-DATA-QUALITY-FORMATION-DATE-MISSING",
                    description="Entity formation date is missing or unparseable. Manual verification required.",
                    source_layer="M4_ANOMALY",
                    severity=EvidenceSeverity.LOW,
                    verified_fact=True,
                    fact_key="DQ:FORMATION_DATE",
                    family="SUBMISSION",
                    details={"raw_formation_date": app.formation_date}
                ))
            elif days < 30:
                evidence.append(EvidenceItem(
                    code="M4-ANOMALY-RAPID-FORMATION",
                    description=(
                        f"Entity formed only {days} days prior to Medicaid enrollment application "
                        f"(formation date: {app.formation_date}). Characteristic of EBV shell entry."
                    ),
                    source_layer="M4_ANOMALY",
                    severity=EvidenceSeverity.HIGH if days < 15 else EvidenceSeverity.MEDIUM,
                    verified_fact=False,
                    fact_key="AGE:RAPID_FORMATION",
                    family="AGE",
                    details={"days_since_formation": days, "formation_date": app.formation_date}
                ))

        # 2. Check Device Fingerprint Sharing (7-day window, distinct TINs)
        if app.device_fingerprint_id:
            other_apps_device = self._count_unrelated_within_window(
                self._device_history[app.device_fingerprint_id], app.application_id, app_date, clean_tin, 7
            )
            if len(other_apps_device) >= self.config.max_submissions_per_device_7days:
                evidence.append(EvidenceItem(
                    code="M4-ANOMALY-DEVICE-SHARING",
                    description=(
                        f"Device fingerprint '{app.device_fingerprint_id}' was used to submit {len(other_apps_device) + 1} "
                        "distinct provider applications within 7 days. Indicates coordinated application mill."
                    ),
                    source_layer="M4_ANOMALY",
                    severity=EvidenceSeverity.HIGH,
                    verified_fact=False,
                    fact_key=f"DEV:{app.device_fingerprint_id}",
                    family="SUBMISSION",
                    details={
                        "device_id": app.device_fingerprint_id,
                        "linked_applications": other_apps_device,
                        "recent_count": len(other_apps_device) + 1,
                        "window_days": 7
                    }
                ))
            if record:
                self._record_entry(self._device_history, app.device_fingerprint_id, app.application_id, app_date, clean_tin)

        # 3. Check IP Address Velocity (7-day window, distinct TINs)
        if app.submission_ip_address:
            other_apps_ip = self._count_unrelated_within_window(
                self._ip_history[app.submission_ip_address], app.application_id, app_date, clean_tin, 7
            )
            if len(other_apps_ip) >= self.config.max_submissions_per_ip_7days:
                evidence.append(EvidenceItem(
                    code="M4-ANOMALY-IP-VELOCITY",
                    description=(
                        f"IP address '{app.submission_ip_address}' generated {len(other_apps_ip) + 1} provider applications "
                        "within 7-day submission window."
                    ),
                    source_layer="M4_ANOMALY",
                    severity=EvidenceSeverity.HIGH,
                    verified_fact=False,
                    fact_key=f"IP:{app.submission_ip_address}",
                    family="SUBMISSION",
                    details={
                        "ip_address": app.submission_ip_address,
                        "linked_applications": other_apps_ip,
                        "recent_count": len(other_apps_ip) + 1,
                        "window_days": 7
                    }
                ))
            if record:
                self._record_entry(self._ip_history, app.submission_ip_address, app.application_id, app_date, clean_tin)

        # 4. Check Email Sharing (30-day window, distinct TINs)
        if app.email:
            email_key = app.email.strip().lower()
            other_apps_email = self._count_unrelated_within_window(
                self._email_history[email_key], app.application_id, app_date, clean_tin, 30
            )
            if len(other_apps_email) >= self.config.max_submissions_per_email_30days:
                evidence.append(EvidenceItem(
                    code="M4-ANOMALY-EMAIL-REUSE",
                    description=f"Contact email '{app.email}' shared across {len(other_apps_email) + 1} distinct provider entities within 30 days.",
                    source_layer="M4_ANOMALY",
                    severity=EvidenceSeverity.MEDIUM,
                    verified_fact=False,
                    fact_key=f"EMAIL:{email_key}",
                    family="SUBMISSION",
                    details={
                        "email": app.email,
                        "linked_applications": other_apps_email,
                        "recent_count": len(other_apps_email) + 1,
                        "window_days": 30
                    }
                ))
            if record:
                self._record_entry(self._email_history, email_key, app.application_id, app_date, clean_tin)

        return evidence

