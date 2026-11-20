"""WORM (Write Once, Read Many) Audit Logger and Replay Engine.

Ensures every automated inference, decision state, and reviewer override
is cryptographically verifiable and auditable under state contract terms
(NC RFP Attachment C Section 5 and ADR-0001).
"""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional

from ..models.entities import ProviderApplication
from ..models.results import AdvisoryScore, AuditRecord, Layer1Decision


class WormAuditLogger:
    """Manages immutable decision audit logs with SHA-256 fingerprinting."""

    def __init__(self, model_version: str = "v1.0.0-phase0"):
        self.model_version = model_version
        self._records: List[AuditRecord] = []
        self._previous_hash: str = "0000000000000000000000000000000000000000000000000000000000000000"

    def compute_feature_vector_sha256(self, app: ProviderApplication, evidence: List[Any]) -> str:
        """Calculate deterministic SHA-256 hash covering all input fields and surfaced evidence."""
        policy_hash = hashlib.sha256(
            (app.policy_manual_text or "").encode("utf-8")
        ).hexdigest() if app.policy_manual_text else ""

        digest_dict = {
            "application_id": app.application_id,
            "npi": app.npi,
            "tin": app.tin,
            "legal_business_name": app.legal_business_name,
            "formation_date": app.formation_date,
            "application_date": app.application_date,
            "service_category": app.service_category,
            "address": app.primary_address.normalized_single_line if app.primary_address else "",
            "phone": app.phone,
            "eft_key": app.eft_account.account_key if app.eft_account else "",
            "device_id": app.device_fingerprint_id,
            "ip_address": app.submission_ip_address,
            "policy_text_sha256": policy_hash,
            "disclosed_persons": sorted([
                f"{p.full_name}:{p.role}:{p.ssn_last4}:{p.date_of_birth}"
                for p in app.disclosed_persons
            ]),
            "evidence_signatures": sorted([
                f"{getattr(e, 'code', str(e))}:{getattr(e, 'severity', '')}:{getattr(e, 'fact_key', '')}"
                for e in evidence
            ])
        }
        serialized = json.dumps(digest_dict, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def log_decision_event(
        self,
        app: ProviderApplication,
        layer1_decision: Layer1Decision,
        advisory_score: AdvisoryScore,
        snapshot_id: str = "SNAP-2026-02-01"
    ) -> AuditRecord:
        """Create and store immutable audit record for an application decision event."""
        now_iso = datetime.now(timezone.utc).isoformat()
        feat_sha = self.compute_feature_vector_sha256(app, advisory_score.evidence_items)
        rec_id = f"AUDIT-{len(self._records) + 1:06d}"

        # Cryptographic chaining: hash(prev_hash + current_data)
        record_content = (
            f"{self._previous_hash}|{rec_id}|SYSTEM_INFERENCE|{app.application_id}|{now_iso}|{feat_sha}|"
            f"{layer1_decision.status.value}|{advisory_score.priority_score}"
        )
        curr_hash = hashlib.sha256(record_content.encode("utf-8")).hexdigest()
        self._previous_hash = curr_hash

        audit_rec = AuditRecord(
            record_id=rec_id,
            application_id=app.application_id,
            timestamp_utc=now_iso,
            feature_vector_sha256=feat_sha,
            model_version=self.model_version,
            snapshot_id=snapshot_id,
            layer1_status=layer1_decision.status.value,
            advisory_priority_score=advisory_score.priority_score,
            verified_fact_codes=[e.code for e in layer1_decision.verified_facts],
            event_type="SYSTEM_INFERENCE",
            audit_hash=curr_hash
        )
        self._records.append(audit_rec)
        return audit_rec

    def record_reviewer_override(
        self,
        record_id: str,
        reviewer_id: str,
        action: str,
        notes: str
    ) -> AuditRecord:
        """Record human reviewer override as a NEW chained audit event without mutating existing records."""
        matching = [r for r in self._records if r.record_id == record_id]
        if not matching:
            raise KeyError(f"Audit record {record_id} not found")
        
        target = matching[0]
        now_iso = datetime.now(timezone.utc).isoformat()
        override_rec_id = f"AUDIT-{len(self._records) + 1:06d}"

        # Cryptographic chaining: chain includes prior hash and override metadata
        override_content = (
            f"{self._previous_hash}|{override_rec_id}|REVIEWER_DECISION|{target.record_id}|"
            f"{target.application_id}|{reviewer_id}|{action}|{now_iso}|{target.audit_hash}"
        )
        override_hash = hashlib.sha256(override_content.encode("utf-8")).hexdigest()
        self._previous_hash = override_hash

        override_rec = AuditRecord(
            record_id=override_rec_id,
            application_id=target.application_id,
            timestamp_utc=now_iso,
            feature_vector_sha256=target.feature_vector_sha256,
            model_version=self.model_version,
            snapshot_id=target.snapshot_id,
            layer1_status=target.layer1_status,
            advisory_priority_score=target.advisory_priority_score,
            verified_fact_codes=list(target.verified_fact_codes),
            event_type="REVIEWER_DECISION",
            parent_record_id=target.record_id,
            reviewer_id=reviewer_id,
            reviewer_action=action,
            reviewer_notes=notes,
            audit_hash=override_hash
        )
        self._records.append(override_rec)
        return override_rec

    def verify_chain(self) -> bool:
        """Cryptographically verify the entire audit log chain integrity from genesis."""
        expected_prev = "0000000000000000000000000000000000000000000000000000000000000000"
        for rec in self._records:
            if rec.event_type == "REVIEWER_DECISION":
                content = (
                    f"{expected_prev}|{rec.record_id}|REVIEWER_DECISION|{rec.parent_record_id}|"
                    f"{rec.application_id}|{rec.reviewer_id}|{rec.reviewer_action}|{rec.timestamp_utc}|"
                    f"{next((r.audit_hash for r in self._records if r.record_id == rec.parent_record_id), '')}"
                )
            else:
                content = (
                    f"{expected_prev}|{rec.record_id}|SYSTEM_INFERENCE|{rec.application_id}|{rec.timestamp_utc}|"
                    f"{rec.feature_vector_sha256}|{rec.layer1_status}|{rec.advisory_priority_score}"
                )
            computed_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if computed_hash != rec.audit_hash:
                return False
            expected_prev = computed_hash
        return True

    def get_all_records(self) -> List[AuditRecord]:
        return list(self._records)

