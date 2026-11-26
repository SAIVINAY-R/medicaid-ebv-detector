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
import re
import sys
import traceback
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse
import uuid

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
                    "event_type": getattr(r, "event_type", "AUTOMATED_EVALUATION"),
                    "parent_record_id": getattr(r, "parent_record_id", None),
                    "reviewer_id": r.reviewer_id,
                    "reviewer_action": r.reviewer_action,
                    "reviewer_notes": r.reviewer_notes,
                    "timestamp_utc": r.timestamp_utc,
                    "layer1_status": r.layer1_status,
                    "advisory_priority_score": r.advisory_priority_score,
                    "feature_vector_sha256": r.feature_vector_sha256,
                    "audit_hash": r.audit_hash
                }
                for r in reversed(records[-50:])
            ])
            return

        if path == "/api/audit/verify":
            chain_valid = self.server_instance.cp1.audit_logger.verify_chain()
            records = self.server_instance.cp1.audit_logger.get_all_records()
            self._send_json_response(200, {
                "chain_valid": chain_valid,
                "total_records": len(records)
            })
            return

        if path == "/api/db/stats":
            with self.server_instance.cp1.graph_store.lock:
                cur = self.server_instance.cp1.graph_store.conn.cursor()
                cur.execute("SELECT node_type, COUNT(*) as cnt FROM graph_nodes GROUP BY node_type")
                node_counts = {r["node_type"]: r["cnt"] for r in cur.fetchall()}
                cur.execute("SELECT edge_type, COUNT(*) as cnt FROM graph_edges GROUP BY edge_type")
                edge_counts = {r["edge_type"]: r["cnt"] for r in cur.fetchall()}
                cur.execute("SELECT COUNT(*) as total FROM graph_nodes")
                total_nodes = cur.fetchone()["total"]
                cur.execute("SELECT COUNT(*) as total FROM graph_edges")
                total_edges = cur.fetchone()["total"]
            self._send_json_response(200, {
                "total_nodes": total_nodes,
                "total_edges": total_edges,
                "nodes_by_type": node_counts,
                "edges_by_type": edge_counts
            })
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

        if not isinstance(body, dict):
            self._send_json_response(400, {"error": "JSON request body must be an object."})
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

        if path == "/api/db/query":
            sql = (body.get("query") or body.get("sql") or "").strip().rstrip(";").strip()
            if not sql:
                self._send_json_response(400, {"error": "Missing 'sql' query parameter in request body."})
                return
            is_read_prefix = bool(re.match(r"^(SELECT|WITH)\b", sql, re.IGNORECASE))
            has_semicolon = ";" in sql
            has_write_kw = bool(re.search(
                r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|DETACH|CREATE|REPLACE|PRAGMA|VACUUM)\b",
                sql,
                re.IGNORECASE
            ))
            if not is_read_prefix or has_semicolon or has_write_kw:
                self._send_json_response(403, {"error": "Only single-statement read-only SELECT or WITH queries are permitted."})
                return
            try:
                with self.server_instance.cp1.graph_store.lock:
                    cur = self.server_instance.cp1.graph_store.conn.cursor()
                    cur.execute(sql)
                    columns = [d[0] for d in cur.description] if cur.description else []
                    rows = [dict(zip(columns, row)) for row in cur.fetchmany(200)]
                self._send_json_response(200, {
                    "query": sql,
                    "columns": columns,
                    "row_count": len(rows),
                    "rows": rows
                })
            except Exception as e:
                self._send_json_response(400, {"error": f"SQL execution error: {str(e)}"})
            return

        if path == "/api/audit/override":
            record_id = body.get("record_id")
            reviewer_id = body.get("reviewer_id")
            action = body.get("action")
            notes = body.get("notes") or ""
            if not record_id or not reviewer_id or not action:
                self._send_json_response(400, {"error": "Missing required fields: record_id, reviewer_id, action"})
                return
            try:
                new_rec = self.server_instance.cp1.audit_logger.record_reviewer_override(
                    record_id=record_id,
                    reviewer_id=reviewer_id,
                    action=action,
                    notes=notes
                )
                chain_valid = self.server_instance.cp1.audit_logger.verify_chain()
                self._send_json_response(200, {
                    "status": "success",
                    "new_record_id": new_rec.record_id,
                    "parent_record_id": new_rec.parent_record_id,
                    "event_type": new_rec.event_type,
                    "chain_valid": chain_valid,
                    "audit_hash": new_rec.audit_hash
                })
            except KeyError as e:
                self._send_json_response(404, {"error": str(e)})
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                self._send_json_response(500, {"error": f"Override error: {str(e)}"})
            return

        self.send_response(404)
        self.end_headers()


class EBVServer:
    """Encapsulates the HTTP server and pipeline state."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        config: Optional[SystemConfig] = None,
        demo_mode: bool = False
    ):
        self.host = host
        self.port = port
        self.config = config or SystemConfig()
        self.demo_mode = demo_mode

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

        # Pre-seed cohort with legitimate providers only in demo mode (Item 3.10)
        if self.demo_mode:
            for app in self.dataset.applications[:10]:
                self.cp1.evaluate_application(app)

        self.httpd: Optional[ThreadingHTTPServer] = None
        self._idempotency_cache: Dict[str, Dict[str, Any]] = {}

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
        """Process enrollment screening application through Checkpoint 1 (supports dry_run and idempotency_key)."""
        idem_key = payload.get("idempotency_key")
        dry_run = bool(payload.get("dry_run", False))
        if isinstance(idem_key, str) and idem_key and not dry_run and idem_key in self._idempotency_cache:
            return self._idempotency_cache[idem_key]
        raw_addr = payload.get("primary_address")
        addr = None
        if isinstance(raw_addr, dict) and any(
            raw_addr.get(k) for k in ("address_line1", "city", "zip5", "is_cmra", "is_po_box", "is_virtual_mailbox", "is_pmb")
        ):
            addr = AddressRecord(
                address_line1=str(raw_addr.get("address_line1") or "").strip(),
                address_line2=raw_addr.get("address_line2"),
                city=str(raw_addr.get("city") or "Raleigh").strip(),
                state=str(raw_addr.get("state") or "NC").strip(),
                zip5=str(raw_addr.get("zip5") or "27601").strip(),
                is_cmra=bool(raw_addr.get("is_cmra", False)),
                is_po_box=bool(raw_addr.get("is_po_box", False)),
                is_virtual_mailbox=bool(raw_addr.get("is_virtual_mailbox", False)),
                is_pmb=bool(raw_addr.get("is_pmb", False)),
                is_vacant=bool(raw_addr.get("is_vacant", False)),
                dpv_valid=bool(raw_addr.get("dpv_valid", True)),
                rdi=str(raw_addr.get("rdi") or "COMMERCIAL").strip()
            )

        raw_eft = payload.get("eft_account")
        eft = None
        if isinstance(raw_eft, dict):
            routing = str(raw_eft.get("routing_number") or "").strip()
            acct = str(raw_eft.get("account_number") or "").strip()
            if routing or acct:
                eft = EFTAccount(routing_number=routing, account_number=acct)

        persons = []
        for p in (payload.get("disclosed_persons") or []):
            if isinstance(p, dict) and any(p.get(k) for k in ("first_name", "last_name", "ssn_last4")):
                persons.append(PersonDisclosure(
                    first_name=str(p.get("first_name") or "").strip(),
                    last_name=str(p.get("last_name") or "").strip(),
                    ssn_last4=str(p.get("ssn_last4") or "").strip(),
                    date_of_birth=str(p.get("date_of_birth") or "").strip(),
                    role=str(p.get("role") or "OWNER").strip(),
                    direct_ownership_pct=safe_float(p.get("direct_ownership_pct"), 1.0),
                    has_criminal_record_disclosed=bool(p.get("has_criminal_record_disclosed", False))
                ))

        npi = str(payload.get("npi") or "1900000001").strip()
        app_id = payload.get("application_id") or f"APP-{uuid.uuid4().hex[:8].upper()}"
        app = ProviderApplication(
            application_id=app_id,
            npi=npi,
            legal_business_name=str(payload.get("legal_business_name") or "Sample Healthcare LLC").strip(),
            tin=str(payload.get("tin") or "56-1234567").strip(),
            service_category=str(payload.get("service_category") or "BEHAVIORAL_HEALTH").strip(),
            formation_date=str(payload.get("formation_date") or "2026-01-01").strip(),
            application_date=str(payload.get("application_date") or "2026-02-01").strip(),
            phone=str(payload.get("phone") or "").strip(),
            email=str(payload.get("email") or "").strip(),
            primary_address=addr,
            eft_account=eft,
            disclosed_persons=persons,
            device_fingerprint_id=payload.get("device_fingerprint_id") or None,
            submission_ip_address=payload.get("submission_ip_address") or None,
            policy_manual_text=payload.get("policy_manual_text"),
            prior_termination_disclosed=bool(payload.get("prior_termination_disclosed", False)),
            is_change_of_ownership=bool(payload.get("is_change_of_ownership", False)),
            prior_enrolled_npi=payload.get("prior_enrolled_npi") or None,
        )

        # Handle GR-4 Upstream Corporate Hierarchy if provided (normalize >1.0 percentages like 60 -> 0.60)
        holding_org_id = payload.get("holding_org_id")
        upstream_owner_id = payload.get("upstream_owner_id")
        upstream_pct = safe_float(payload.get("upstream_pct"), 0.0)
        if upstream_pct > 1.0:
            upstream_pct = upstream_pct / 100.0
        upstream_pct = max(0.0, min(1.0, upstream_pct))

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

        if dry_run:
            eval_pkg = self.cp1.score_application(app, record_audit=False, enqueue_near_matches=False)
        else:
            eval_pkg = self.cp1.evaluate_application(app)

        response = {
            "application_id": eval_pkg.application_id,
            "dry_run": dry_run,
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
                "feature_contributions": eval_pkg.advisory_score.feature_contributions,
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
                "snapshot_id": eval_pkg.audit_record.snapshot_id,
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
        if isinstance(idem_key, str) and idem_key and not dry_run:
            self._idempotency_cache[idem_key] = response
        return response

    def evaluate_claims(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate claims surveillance through Checkpoint 2."""
        npi = str(payload.get("npi") or "1900000056").strip()
        service_cat = str(payload.get("service_category") or "BEHAVIORAL_HEALTH").strip()
        months = max(1, safe_int(payload.get("months_enrolled"), 3))
        paid = max(0.0, safe_float(payload.get("total_paid_30d"), 186000.0))
        max_hrs = max(0.0, safe_float(payload.get("max_daily_hours"), 36.0))
        inpat = max(0, safe_int(payload.get("inpatient_overlaps"), 3))
        post = max(0, safe_int(payload.get("post_mortem_claims"), 2))
        migrating = max(0, safe_int(payload.get("migrating_patients"), 30))

        # Synthetic claims generation for surveillance evaluation
        claims = []
        num_claims = 20
        total_target_claims = num_claims + migrating
        per_claim_paid = (paid / total_target_claims) if total_target_claims > 0 else 0.0

        for i in range(num_claims):
            is_inpatient = (i < inpat)
            is_post_mortem = (i < post)
            # First 3 claims land on peak day 2026-03-15 totaling exact max_hrs;
            # remaining claims spread cleanly across days 1..14 so clean workdays (<=24h) don't falsely spike.
            if i < 3:
                s_date = "2026-03-15"
                hours = max_hrs / 3.0
            else:
                day_num = ((i - 3) % 14) + 1
                s_date = f"2026-03-{day_num:02d}"
                hours = 2.0

            claims.append(ClaimRecord(
                claim_id=f"CLM-SIM-{i:03d}",
                billing_provider_npi=npi,
                rendering_clinician_npi=f"CLIN-{npi[-4:]}",
                beneficiary_id=f"BEN-SIM-{i:03d}",
                service_date=s_date,
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
            "surveillance_priority_score": sig.surveillance_priority_score,
            "recommended_cpmm_action": sig.recommended_cpmm_action,
            "trigger_unannounced_site_visit": sig.trigger_unannounced_site_visit,
            "trigger_payment_suspension": sig.trigger_payment_suspension,
            "ramp_result": {
                "total_paid_30d": sig.ramp_result.total_paid_30d,
                "cohort_median_paid_30d": sig.ramp_result.cohort_median_paid_30d,
                "robust_z_score": sig.ramp_result.robust_z_score,
                "is_ramp_anomaly": sig.ramp_result.is_ramp_anomaly,
                "cusum_alarm": sig.ramp_result.cusum_alarm,
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

