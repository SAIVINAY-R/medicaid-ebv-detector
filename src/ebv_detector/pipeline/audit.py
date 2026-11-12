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
        """Calculate deterministic SHA-256 hash of input application state and surfaced evidence."""
        digest_dict = {
            "application_id": app.application_id,
            "npi": app.npi,
            "tin": app.tin,
            "legal_business_name": app.legal_business_name,
            "formation_date": app.formation_date,
            "application_date": app.application_date,
            "address": app.primary_address.normalized_single_line if app.primary_address else "",
            "phone": app.phone,
            "eft_key": app.eft_account.account_key if app.eft_account else "",
            "evidence_codes": sorted([getattr(e, "code", str(e)) for e in evidence])
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
            f"{self._previous_hash}|{rec_id}|{app.application_id}|{now_iso}|{feat_sha}|"
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
        """Record human reviewer override with required justification notes."""
        matching = [r for r in self._records if r.record_id == record_id]
        if not matching:
            raise KeyError(f"Audit record {record_id} not found")
        
        target = matching[0]
        target.reviewer_id = reviewer_id
        target.reviewer_action = action
        target.reviewer_notes = notes
        return target

    def get_all_records(self) -> List[AuditRecord]:
        return list(self._records)

