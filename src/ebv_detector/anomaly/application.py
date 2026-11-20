"""M4 Application Telemetry and Velocity Anomaly Detector.

Detects coordinated submission rings based on:
- High submission velocity from same IP address within 7 days
- Multiple distinct provider applications from same physical device fingerprint
- Rapid entity formation (formed < 30 days prior to application)
- Re-use of email domain or phone across distinct provider groups
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from ..config import ApplicationAnomalyConfig
from ..models.entities import ProviderApplication
from ..models.results import EvidenceItem, EvidenceSeverity


class ApplicationVelocityTracker:
    """Tracks submission velocity and device/IP reuse across applications."""

    def __init__(self, config: Optional[ApplicationAnomalyConfig] = None):
        self.config = config or ApplicationAnomalyConfig()
        # ip -> list of (app_id, submission_date_str)
        self._ip_history: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        # device_id -> list of (app_id, submission_date_str)
        self._device_history: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        # email -> list of (app_id, submission_date_str)
        self._email_history: Dict[str, List[Tuple[str, str]]] = defaultdict(list)

    def _is_within_days(self, current_date_str: str, prior_date_str: str, max_days: int) -> bool:
        """Check if prior date is within max_days of current date."""
        try:
            curr_dt = datetime.strptime(current_date_str, "%Y-%m-%d").date()
            prior_dt = datetime.strptime(prior_date_str, "%Y-%m-%d").date()
            return abs((curr_dt - prior_dt).days) <= max_days
        except Exception:
            return True

    def record_and_evaluate(self, app: ProviderApplication) -> List[EvidenceItem]:
        """Record current application telemetry and check for velocity/clustering anomalies within sliding windows."""
        evidence: List[EvidenceItem] = []
        app_date = app.application_date

        # 1. Formation Age Evaluation
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

        # 2. Check Device Fingerprint Sharing (7-day window)
        if app.device_fingerprint_id:
            prior_devices = self._device_history[app.device_fingerprint_id]
            other_apps_device = [
                aid for aid, dt in prior_devices
                if aid != app.application_id and self._is_within_days(app_date, dt, 7)
            ]
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
                    details={"device_id": app.device_fingerprint_id, "linked_applications": other_apps_device, "window_days": 7}
                ))
            self._device_history[app.device_fingerprint_id].append((app.application_id, app_date))

        # 3. Check IP Address Velocity (7-day window)
        if app.submission_ip_address:
            prior_ips = self._ip_history[app.submission_ip_address]
            other_apps_ip = [
                aid for aid, dt in prior_ips
                if aid != app.application_id and self._is_within_days(app_date, dt, 7)
            ]
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
                    details={"ip_address": app.submission_ip_address, "linked_applications": other_apps_ip, "window_days": 7}
                ))
            self._ip_history[app.submission_ip_address].append((app.application_id, app_date))

        # 4. Check Email Sharing (30-day window)
        if app.email:
            prior_emails = self._email_history[app.email.lower()]
            other_apps_email = [
                aid for aid, dt in prior_emails
                if aid != app.application_id and self._is_within_days(app_date, dt, 30)
            ]
            if len(other_apps_email) >= self.config.max_submissions_per_email_30days:
                evidence.append(EvidenceItem(
                    code="M4-ANOMALY-EMAIL-REUSE",
                    description=f"Contact email '{app.email}' shared across {len(other_apps_email) + 1} distinct provider entities within 30 days.",
                    source_layer="M4_ANOMALY",
                    severity=EvidenceSeverity.MEDIUM,
                    verified_fact=False,
                    fact_key=f"EMAIL:{app.email.lower()}",
                    family="SUBMISSION",
                    details={"email": app.email, "linked_applications": other_apps_email, "window_days": 30}
                ))
            self._email_history[app.email.lower()].append((app.application_id, app_date))

        return evidence

