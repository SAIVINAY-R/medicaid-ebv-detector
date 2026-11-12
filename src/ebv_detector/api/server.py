"""HTTP REST API and Web Dashboard Server for EBV Fraud Intelligence.

Pure standard library HTTP server (ThreadingHTTPServer) providing:
- Web dashboard static asset delivery
- RESTful JSON evaluation APIs for enrollment (Checkpoint 1) and claims (Checkpoint 2)
- Zero external Python web dependencies (100% self-contained and auditable)
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from ..config import SystemConfig
from ..models.entities import (
    AddressRecord,
    ClaimRecord,
    EFTAccount,
    OwnershipEdge,
    PersonDisclosure,
    ProviderApplication,
    SeedRecord,
)
from ..pipeline.checkpoint1 import Checkpoint1Pipeline
from ..pipeline.checkpoint2 import Checkpoint2Pipeline
from ..synthetic.generator import SyntheticEBVGenerator

STATIC_DIR = Path(__file__).parent / "static"


def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert any value (None, empty string, NaN) to float."""
    if val is None or val == "":
        return default
    try:
        f = float(val)
        return default if math.isnan(f) else f
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    """Safely convert any value to int."""
    if val is None or val == "":
        return default
    try:
        f = float(val)
        return default if math.isnan(f) else int(f)
    except (ValueError, TypeError):
        return default


class EBVRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for EBV fraud detection system."""

    server_instance: "EBVServer"

    def log_message(self, format, *args):
        # Override to keep server output clean unless needed
        pass

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def _send_json_response(self, status_code: int, data: Any):
        payload = json.dumps(data, indent=2, default=lambda o: getattr(o, "__dict__", str(o)))
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            index_path = STATIC_DIR / "index.html"
            if index_path.exists():
                content = index_path.read_text(encoding="utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Static assets not found.")
            return

        if path in ("/api/health", "/health", "/healthz"):
            self._send_json_response(200, {
                "status": "healthy",
                "service": "Medicaid Provider Fraud Intelligence API",
                "version": self.server_instance.config.model_version
            })
            return

        if path == "/api/scenarios":
            self._send_json_response(200, self.server_instance.get_sample_scenarios())
            return

        if path == "/api/seeds":
            seeds = [
                {
                    "seed_id": s.seed_id,
                    "identifier_type": s.identifier_type,
                    "identifier_value": s.identifier_value,
                    "source": s.source,
                    "reason": s.reason,
                    "jurisdiction": s.jurisdiction
                }
                for s in self.server_instance.dataset.seed_records
            ]
            self._send_json_response(200, seeds)
            return

        if path == "/api/audit-trail":
            records = self.server_instance.cp1.audit_logger.get_all_records()
            self._send_json_response(200, [
                {
                    "record_id": r.record_id,
                    "application_id": r.application_id,
                    "timestamp_utc": r.timestamp_utc,
                    "layer1_status": r.layer1_status,
                    "advisory_priority_score": r.advisory_priority_score,
                    "feature_vector_sha256": r.feature_vector_sha256,
                    "audit_hash": r.audit_hash
                }
                for r in reversed(records[-50:])
            ])
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception as e:
            self._send_json_response(400, {"error": f"Invalid JSON payload: {str(e)}"})
            return

        if path == "/api/evaluate/enrollment":
            try:
                res = self.server_instance.evaluate_enrollment(body)
                self._send_json_response(200, res)
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                self._send_json_response(500, {"error": f"Evaluation error: {str(e)}"})
            return

        if path == "/api/evaluate/claims":
            try:
                res = self.server_instance.evaluate_claims(body)
                self._send_json_response(200, res)
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                self._send_json_response(500, {"error": f"Claims surveillance error: {str(e)}"})
            return

        self.send_response(404)
        self.end_headers()


class EBVServer:
    """Encapsulates the HTTP server and pipeline state."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080, config: Optional[SystemConfig] = None):
        self.host = host
        self.port = port
        self.config = config or SystemConfig()

        # Seed dataset
        generator = SyntheticEBVGenerator(seed=42)
        self.dataset = generator.generate_benchmark_suite()

        # Initialize pipelines
        self.cp1 = Checkpoint1Pipeline(self.config, self.dataset.seed_records)
        self.cp1.ingest_ownership_edges(self.dataset.ownership_edges)

        self.cp2 = Checkpoint2Pipeline(self.config)
        for s in self.dataset.seed_records:
            if s.identifier_type == "NPI":
                self.cp2.register_sanctioned_provider(s.identifier_value)
        self.cp2.ingest_claims(self.dataset.claims)

        # Pre-seed cohort with legitimate providers
        for app in self.dataset.applications[:10]:
            self.cp1.evaluate_application(app)

        self.httpd: Optional[ThreadingHTTPServer] = None

    def get_sample_scenarios(self) -> Dict[str, Any]:
        """Format demonstration scenarios for UI dropdown."""
        scenarios = {}

        # 1. Control Legitimate Provider
        legit = self.dataset.applications[0]
        scenarios["control"] = {
            "legal_business_name": legit.legal_business_name,
            "npi": legit.npi,
            "tin": legit.tin,
            "service_category": legit.service_category,
            "formation_date": legit.formation_date,
            "application_date": legit.application_date,
            "phone": legit.phone,
            "primary_address": legit.primary_address.__dict__ if legit.primary_address else {},
            "eft_account": legit.eft_account.__dict__ if legit.eft_account else {},
            "disclosed_persons": [p.__dict__ for p in legit.disclosed_persons],
            "device_fingerprint_id": legit.device_fingerprint_id,
            "policy_manual_text": legit.policy_manual_text
        }

        # 2. Scenario 1 (Mailbox shell)
        s1 = next(a for a in self.dataset.applications if a.ground_truth_label == "EBV_FRAUD" and a.primary_address and a.primary_address.is_cmra)
        scenarios["scenario1"] = {
            "legal_business_name": s1.legal_business_name,
            "npi": s1.npi,
            "tin": s1.tin,
            "service_category": s1.service_category,
            "formation_date": s1.formation_date,
            "application_date": s1.application_date,
            "phone": s1.phone,
            "primary_address": s1.primary_address.__dict__ if s1.primary_address else {},
            "eft_account": s1.eft_account.__dict__ if s1.eft_account else {},
            "disclosed_persons": [p.__dict__ for p in s1.disclosed_persons],
            "device_fingerprint_id": s1.device_fingerprint_id,
            "policy_manual_text": s1.policy_manual_text
        }

        # 3. Scenario 2 (Nominee owner & Excluded felon holding co)
        s2 = next(a for a in self.dataset.applications if "HORIZON" in a.legal_business_name)
        scenarios["scenario2"] = {
            "legal_business_name": s2.legal_business_name,
            "npi": s2.npi,
            "tin": s2.tin,
            "service_category": s2.service_category,
            "formation_date": s2.formation_date,
            "application_date": s2.application_date,
            "phone": s2.phone,
            "primary_address": s2.primary_address.__dict__ if s2.primary_address else {},
            "eft_account": s2.eft_account.__dict__ if s2.eft_account else {},
            "disclosed_persons": [p.__dict__ for p in s2.disclosed_persons],
            "device_fingerprint_id": s2.device_fingerprint_id,
            "holding_org_id": "ORG-HOLDING-CAROLINA-HEALTH",
            "upstream_owner_id": "PERSON-EXCLUDED-8821",
            "upstream_pct": 60,
            "policy_manual_text": s2.policy_manual_text
        }

        # 4. Scenario 3 (Phoenix EFT re-entry)
        s3 = next(a for a in self.dataset.applications if "BEACON" in a.legal_business_name)
        scenarios["scenario3"] = {
            "legal_business_name": s3.legal_business_name,
            "npi": s3.npi,
            "tin": s3.tin,
            "service_category": s3.service_category,
            "formation_date": s3.formation_date,
            "application_date": s3.application_date,
            "phone": s3.phone,
            "primary_address": s3.primary_address.__dict__ if s3.primary_address else {},
            "eft_account": s3.eft_account.__dict__ if s3.eft_account else {},
            "disclosed_persons": [p.__dict__ for p in s3.disclosed_persons],
            "device_fingerprint_id": s3.device_fingerprint_id,
            "policy_manual_text": s3.policy_manual_text
        }

        # 5. Scenario 4 (Cloned policy manual)
        s4 = next(a for a in self.dataset.applications if "SERENITY" in a.legal_business_name)
        scenarios["scenario4"] = {
            "legal_business_name": s4.legal_business_name,
            "npi": s4.npi,
            "tin": s4.tin,
            "service_category": s4.service_category,
            "formation_date": s4.formation_date,
            "application_date": s4.application_date,
            "phone": s4.phone,
            "primary_address": s4.primary_address.__dict__ if s4.primary_address else {},
            "eft_account": s4.eft_account.__dict__ if s4.eft_account else {},
            "disclosed_persons": [p.__dict__ for p in s4.disclosed_persons],
            "device_fingerprint_id": s4.device_fingerprint_id,
            "policy_manual_text": s4.policy_manual_text
        }

        return scenarios

    def evaluate_enrollment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Process enrollment screening application through Checkpoint 1."""
        addr_data = payload.get("primary_address", {})
        addr = AddressRecord(
            address_line1=addr_data.get("address_line1", ""),
            address_line2=addr_data.get("address_line2"),
            city=addr_data.get("city", "Raleigh"),
            state=addr_data.get("state", "NC"),
            zip5=addr_data.get("zip5", "27601"),
            is_cmra=bool(addr_data.get("is_cmra", False)),
            is_po_box=bool(addr_data.get("is_po_box", False)),
            is_virtual_mailbox=bool(addr_data.get("is_virtual_mailbox", False)),
            is_pmb=bool(addr_data.get("is_pmb", False)),
            rdi=addr_data.get("rdi", "COMMERCIAL")
        )

        eft_data = payload.get("eft_account", {})
        eft = EFTAccount(
            routing_number=eft_data.get("routing_number", ""),
            account_number=eft_data.get("account_number", "")
        )

        persons = []
        for p in payload.get("disclosed_persons", []):
            if isinstance(p, dict):
                persons.append(PersonDisclosure(
                    first_name=p.get("first_name") or "Victor",
                    last_name=p.get("last_name") or "Vanish",
                    ssn_last4=str(p.get("ssn_last4") or "4912"),
                    date_of_birth=str(p.get("date_of_birth") or "1992-04-18"),
                    role=p.get("role") or "OWNER",
                    direct_ownership_pct=safe_float(p.get("direct_ownership_pct"), 1.0)
                ))

        npi = str(payload.get("npi") or "1900000001").strip()
        app = ProviderApplication(
            application_id=f"APP-{npi[-6:]}" if len(npi) >= 6 else "APP-000001",
            npi=npi,
            legal_business_name=payload.get("legal_business_name") or "Sample Healthcare LLC",
            tin=str(payload.get("tin") or "56-1234567"),
            service_category=payload.get("service_category") or "BEHAVIORAL_HEALTH",
            formation_date=payload.get("formation_date") or "2026-01-01",
            application_date=payload.get("application_date") or "2026-02-01",
            phone=payload.get("phone") or "919-555-8822",
            primary_address=addr,
            eft_account=eft,
            disclosed_persons=persons,
            device_fingerprint_id=payload.get("device_fingerprint_id"),
            policy_manual_text=payload.get("policy_manual_text")
        )

        # Handle GR-4 Upstream Corporate Hierarchy if provided
        holding_org_id = payload.get("holding_org_id")
        upstream_owner_id = payload.get("upstream_owner_id")
        upstream_pct = safe_float(payload.get("upstream_pct"), 0.0)

        if holding_org_id and upstream_owner_id and upstream_pct > 0.0:
            edge1 = OwnershipEdge(
                parent_entity_id=holding_org_id,
                child_entity_id=npi,
                ownership_percentage=0.80,
                effective_date="2025-01-01"
            )
            edge2 = OwnershipEdge(
                parent_entity_id=upstream_owner_id,
                child_entity_id=holding_org_id,
                ownership_percentage=upstream_pct,
                effective_date="2025-01-01"
            )
            self.cp1.ingest_ownership_edges([edge1, edge2])

        eval_pkg = self.cp1.evaluate_application(app)

        return {
            "application_id": eval_pkg.application_id,
            "layer1_decision": {
                "status": eval_pkg.layer1_decision.status.value,
                "assigned_aca_risk_tier": eval_pkg.layer1_decision.assigned_aca_risk_tier,
                "statutory_grounds": eval_pkg.layer1_decision.statutory_grounds,
                "can_auto_approve": eval_pkg.layer1_decision.can_auto_approve
            },
            "advisory_score": {
                "priority_score": eval_pkg.advisory_score.priority_score,
                "composite_risk_level": eval_pkg.advisory_score.composite_risk_level,
                "explanation_summary": eval_pkg.advisory_score.explanation_summary,
                "recommended_review_actions": eval_pkg.advisory_score.recommended_review_actions,
                "evidence_items": [
                    {
                        "code": e.code,
                        "description": e.description,
                        "source_layer": e.source_layer,
                        "severity": e.severity.value,
                        "verified_fact": e.verified_fact
                    }
                    for e in eval_pkg.advisory_score.evidence_items
                ]
            },
            "audit_record": {
                "record_id": eval_pkg.audit_record.record_id,
                "timestamp_utc": eval_pkg.audit_record.timestamp_utc,
                "feature_vector_sha256": eval_pkg.audit_record.feature_vector_sha256,
                "audit_hash": eval_pkg.audit_record.audit_hash
            },
            "indirect_ownership": [
                {
                    "upstream_entity_id": ind.upstream_entity_id,
                    "effective_indirect_pct": ind.effective_indirect_pct,
                    "meets_5pct_statutory_threshold": ind.meets_5pct_statutory_threshold,
                    "is_upstream_excluded_seed": ind.is_upstream_excluded_seed,
                    "seed_reason": ind.seed_reason
                }
                for ind in eval_pkg.indirect_ownership
            ]
        }

    def evaluate_claims(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate claims surveillance through Checkpoint 2."""
        npi = str(payload.get("npi") or "1900000056").strip()
        service_cat = payload.get("service_category") or "BEHAVIORAL_HEALTH"
        months = safe_int(payload.get("months_enrolled"), 3)
        paid = safe_float(payload.get("total_paid_30d"), 186000.0)
        max_hrs = safe_float(payload.get("max_daily_hours"), 36.0)
        inpat = safe_int(payload.get("inpatient_overlaps"), 3)
        post = safe_int(payload.get("post_mortem_claims"), 2)
        migrating = safe_int(payload.get("migrating_patients"), 30)

        # Synthetic claims generation for surveillance evaluation
        claims = []
        num_claims = 20
        per_claim_paid = paid / num_claims

        for i in range(num_claims):
            is_inpatient = (i < inpat)
            is_post_mortem = (i < post)
            hours = (max_hrs / 3.0) if i < 3 else 2.0

            claims.append(ClaimRecord(
                claim_id=f"CLM-SIM-{i:03d}",
                billing_provider_npi=npi,
                rendering_clinician_npi=f"CLIN-{npi[-4:]}",
                beneficiary_id=f"BEN-SIM-{i:03d}",
                service_date="2026-03-15",
                procedure_code="H0015",
                units_billed=hours * 4.0,
                paid_amount=per_claim_paid,
                service_hours=hours,
                inpatient_overlap=is_inpatient,
                beneficiary_deceased=is_post_mortem
            ))

        # Handle migrating patients from suspended provider
        if migrating > 0:
            suspended_npi = "1487654321"
            self.cp2.register_sanctioned_provider(suspended_npi)
            prior_claims = []
            for b_idx in range(migrating):
                b_id = f"BEN-MIG-{b_idx}"
                prior_claims.append(ClaimRecord(
                    claim_id=f"CLM-PRIOR-{b_idx}",
                    billing_provider_npi=suspended_npi,
                    rendering_clinician_npi="CLIN-SUSP",
                    beneficiary_id=b_id,
                    service_date="2026-01-10",
                    procedure_code="H0015",
                    units_billed=4.0,
                    paid_amount=100.0,
                    service_hours=1.0
                ))
                claims.append(ClaimRecord(
                    claim_id=f"CLM-NEW-MIG-{b_idx}",
                    billing_provider_npi=npi,
                    rendering_clinician_npi=f"CLIN-{npi[-4:]}",
                    beneficiary_id=b_id,
                    service_date="2026-03-20",
                    procedure_code="H0015",
                    units_billed=4.0,
                    paid_amount=per_claim_paid,
                    service_hours=1.0
                ))
            self.cp2.ingest_claims(prior_claims)

        dummy_app = ProviderApplication(
            application_id=f"APP-{npi[-6:]}",
            npi=npi,
            legal_business_name=f"Provider {npi}",
            tin="56-9999999",
            service_category=service_cat
        )

        sig = self.cp2.evaluate_provider_early_billing(dummy_app, months_enrolled=months, claims=claims)

        return {
            "provider_npi": sig.provider_npi,
            "months_enrolled": sig.months_enrolled,
            "recommended_cpmm_action": sig.recommended_cpmm_action,
            "trigger_unannounced_site_visit": sig.trigger_unannounced_site_visit,
            "trigger_payment_suspension": sig.trigger_payment_suspension,
            "ramp_result": {
                "total_paid_30d": sig.ramp_result.total_paid_30d,
                "cohort_median_paid_30d": sig.ramp_result.cohort_median_paid_30d,
                "robust_z_score": sig.ramp_result.robust_z_score,
                "is_ramp_anomaly": sig.ramp_result.is_ramp_anomaly,
                "impossible_hours_count": sig.ramp_result.impossible_hours_count,
                "inpatient_overlap_count": sig.ramp_result.inpatient_overlap_count,
                "deceased_overlap_count": sig.ramp_result.deceased_overlap_count
            },
            "evidence_items": [
                {
                    "code": e.code,
                    "description": e.description,
                    "severity": e.severity.value,
                    "verified_fact": e.verified_fact
                }
                for e in sig.total_evidence_items
            ]
        }

    def start(self):
        """Start HTTP server."""
        EBVRequestHandler.server_instance = self
        self.httpd = ThreadingHTTPServer((self.host, self.port), EBVRequestHandler)
        print(f"[*] Medicaid EBV Fraud Intelligence API & Dashboard running at http://{self.host}:{self.port}/")
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[*] Shutting down server...")
        finally:
            self.httpd.server_close()


def run_server(host: Optional[str] = None, port: Optional[int] = None):
    if host is None:
        host = os.environ.get("HOST", "0.0.0.0")
    if port is None:
        port = int(os.environ.get("PORT", "8080"))
    server = EBVServer(host=host, port=port)
    server.start()


if __name__ == "__main__":
    run_server()

