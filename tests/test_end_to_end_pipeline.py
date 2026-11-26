"""Integration tests verifying end-to-end Checkpoint 1, Checkpoint 2, and HTTP API handler pipelines."""

import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from ebv_detector.api.server import EBVRequestHandler, EBVServer
from ebv_detector.config import GraphConfig, SystemConfig
from ebv_detector.entity_resolution.near_match_queue import AdjudicationAction
from ebv_detector.evaluation.harness import EvaluationHarness
from ebv_detector.graph.store import GraphStore
from ebv_detector.models.entities import AddressRecord, ClaimRecord, EFTAccount, ProviderApplication
from ebv_detector.models.results import DecisionStatus
from ebv_detector.pipeline.checkpoint1 import Checkpoint1Pipeline
from ebv_detector.pipeline.checkpoint2 import Checkpoint2Pipeline
from ebv_detector.synthetic.generator import SyntheticEBVGenerator


class TestEndToEndPipeline(unittest.TestCase):

    def test_full_benchmark_execution_and_metrics(self):
        generator = SyntheticEBVGenerator(seed=42)
        dataset = generator.generate_benchmark_suite()

        harness = EvaluationHarness()
        report = harness.run_benchmark(dataset)

        # Ensure high detection performance across all fraud scenarios
        self.assertGreaterEqual(report.precision, 0.90)
        self.assertGreaterEqual(report.recall, 0.90)
        self.assertGreaterEqual(report.f1_score, 0.90)
        self.assertLess(report.false_positive_rate, 0.05)
        self.assertLess(report.average_latency_ms, 50.0) # Sub-50ms execution per application
        self.assertGreater(report.precision_at_10, 0.70)
        self.assertGreaterEqual(report.pr_auc, 0.95)
        self.assertGreaterEqual(report.score_only_ablation_f1, 0.85)

        # Verify all specific scenarios passed
        for scen, detected in report.scenario_detection_rates.items():
            self.assertTrue(detected, f"Scenario failed detection: {scen}")

    def test_full_ebv_lifecycle_enrollment_to_claims_adjudication_and_worm_chain(self):
        """End-to-end sequential lifecycle: CP1 ring ingestion -> adjudication -> CP2 claims -> WORM -> SQLite."""
        ds = SyntheticEBVGenerator(seed=42).generate_benchmark_suite()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "e2e_lifecycle.db")
            cfg = SystemConfig(graph=GraphConfig(sqlite_db_path=db_path))
            cp1 = Checkpoint1Pipeline(cfg, ds.seed_records)
            cp2 = Checkpoint2Pipeline(cfg)

            try:
                cp1.ingest_ownership_edges(ds.ownership_edges)
                for s in ds.seed_records:
                    if s.identifier_type == "NPI":
                        cp2.register_sanctioned_provider(s.identifier_value)
                cp2.ingest_claims(ds.claims)
                for app in ds.applications:
                    if ds.ground_truth.get(app.application_id) != "EBV_FRAUD":
                        app_claims = [c for c in ds.claims if c.billing_provider_npi == app.npi]
                        if app_claims:
                            cp2.ramp_detector.register_peer_monthly_volume(
                                app.service_category, 3, sum(c.paid_amount for c in app_claims)
                            )

                # Step 1: Ingest full Cohort through Checkpoint 1
                for app in ds.applications:
                    cp1.evaluate_application(app)

                # Step 2: Verify early ring member was re-scored when later ring members arrived
                ring_apps = [
                    a for a in ds.applications
                    if any(k in a.legal_business_name.upper() for k in ("SERENITY", "TRANQUILITY", "HARMONY"))
                ]
                for ring_app in ring_apps:
                    latest = cp1.get_latest_evaluation(ring_app.application_id)
                    self.assertIsNotNone(latest)
                    self.assertGreater(latest.advisory_score.priority_score, 75.0)
                    self.assertEqual(latest.advisory_score.composite_risk_level, "CRITICAL")

                # Step 3: Reviewer override on WORM audit trail preserves chain integrity
                beacon = next(a for a in ds.applications if "BEACON" in a.legal_business_name.upper())
                beacon_eval = cp1.get_latest_evaluation(beacon.application_id)
                self.assertEqual(beacon_eval.layer1_decision.status, DecisionStatus.GROUNDS_PENDING_HUMAN_DECISION)
                override_rec = cp1.audit_logger.record_reviewer_override(
                    record_id=beacon_eval.audit_record.record_id,
                    reviewer_id="PI_SUPERVISOR_01",
                    action="DENIED",
                    notes="Verified 42 CFR 455.416(a) EFT link to terminated entity."
                )
                self.assertEqual(override_rec.parent_record_id, beacon_eval.audit_record.record_id)
                self.assertTrue(cp1.audit_logger.verify_chain())

                # Step 4: Near-match candidate enqueue + DECOUPLE feedback loop
                nm_a = ProviderApplication(
                    application_id="APP-E2E-NM-1",
                    npi="1995000001",
                    legal_business_name="Piedmont Care Clinic LLC",
                    tin="56-9500001",
                    formation_date="2019-01-01",
                    application_date="2026-02-01",
                    phone="919-555-6100",
                    primary_address=AddressRecord("910 Health Way", "Durham", "NC", "27701", rdi="COMMERCIAL"),
                    eft_account=EFTAccount("053100099", "5566778899"),
                )
                nm_b = ProviderApplication(
                    application_id="APP-E2E-NM-2",
                    npi="1995000002",
                    legal_business_name="Durham Wellness Clinic LLC",
                    tin="56-9500002",
                    formation_date="2019-01-01",
                    application_date="2026-02-01",
                    phone="919-555-6100",
                    primary_address=AddressRecord("910 Health Way", "Durham", "NC", "27701", rdi="COMMERCIAL"),
                    eft_account=EFTAccount("053100099", "5566778899"),
                )
                cp1.evaluate_application(nm_a)
                nm_pkg = cp1.evaluate_application(nm_b)
                self.assertEqual(len(nm_pkg.near_matches), 1)
                decouple_res = cp1.adjudicate_near_match(
                    nm_pkg.near_matches[0].item_id, AdjudicationAction.DECOUPLE, "DESK_REV_02", "Distinct owners"
                )
                self.assertEqual(decouple_res["updated_evaluations"]["APP-E2E-NM-2"].advisory_score.priority_score, 0.0)
                self.assertTrue(cp1.audit_logger.verify_chain())

                # Step 5: Checkpoint 2 Claims Surveillance on Scenario 5 & Scenario 6
                scen5_app = next(a for a in ds.applications if "TRIANGLE CRISIS" in a.legal_business_name.upper())
                scen5_claims = [c for c in ds.claims if c.billing_provider_npi == scen5_app.npi]
                sig5 = cp2.evaluate_provider_early_billing(scen5_app, months_enrolled=3, claims=scen5_claims)
                self.assertTrue(sig5.trigger_payment_suspension)
                self.assertTrue(sig5.trigger_unannounced_site_visit)
                self.assertGreater(sig5.surveillance_priority_score, 85.0)

                scen6_app = next(a for a in ds.applications if "NEW DAWN" in a.legal_business_name.upper())
                scen6_claims = [c for c in ds.claims if c.billing_provider_npi == scen6_app.npi]
                sig6 = cp2.evaluate_provider_early_billing(scen6_app, months_enrolled=3, claims=scen6_claims)
                self.assertTrue(sig6.trigger_payment_suspension)

                # Step 6: Re-open persisted SQLite DB independently and verify graph durability
                cp1.graph_store.conn.close()
                reopened = GraphStore(db_path=db_path)
                try:
                    cur = reopened.conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM graph_nodes WHERE is_seed = 1")
                    self.assertGreaterEqual(cur.fetchone()[0], 6)
                    cur.execute("SELECT COUNT(*) FROM graph_nodes WHERE node_id = ?", (beacon.npi,))
                    self.assertEqual(cur.fetchone()[0], 1)
                finally:
                    reopened.conn.close()
            finally:
                try:
                    cp1.graph_store.conn.close()
                except Exception:
                    pass

    def test_live_http_api_end_to_end_workflows(self):
        """Exercise full HTTP/JSON request parsing, routing, headers, and status codes through EBVRequestHandler."""
        srv = EBVServer(host="127.0.0.1", port=0)

        class _IsolatedHandler(EBVRequestHandler):
            server_instance = srv

        class _DummyServer:
            pass

        def _http_exchange(method: str, path: str, payload=None):
            client_sock, server_sock = socket.socketpair()
            dummy = _DummyServer()

            def _serve_one():
                try:
                    _IsolatedHandler(server_sock, ("127.0.0.1", 50000), dummy)
                finally:
                    try:
                        server_sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    server_sock.close()

            t = threading.Thread(target=_serve_one, daemon=True)
            t.start()
            try:
                body_bytes = json.dumps(payload).encode("utf-8") if payload is not None else b""
                headers = [
                    f"{method} {path} HTTP/1.1",
                    "Host: localhost",
                    "Connection: close",
                ]
                if payload is not None:
                    headers.append("Content-Type: application/json")
                    headers.append(f"Content-Length: {len(body_bytes)}")
                raw_req = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + body_bytes
                client_sock.sendall(raw_req)
                client_sock.shutdown(socket.SHUT_WR)

                resp = http.client.HTTPResponse(client_sock)
                resp.begin()
                raw_resp = resp.read().decode("utf-8")
                return resp.status, resp.getheader("Content-Type", ""), raw_resp
            finally:
                client_sock.close()
                t.join(timeout=2)

        def http_get(path: str):
            return _http_exchange("GET", path)

        def http_post(path: str, payload: dict):
            status, _, raw = _http_exchange("POST", path, payload)
            return status, json.loads(raw)

        try:
            # 1. Static dashboard & health & metadata GET routes
            status, ctype, html_body = http_get("/")
            self.assertEqual(status, 200)
            self.assertIn("text/html", ctype)
            self.assertIn("Medicaid Provider Fraud Intelligence", html_body)

            status, _, health_raw = http_get("/health")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(health_raw)["status"], "healthy")

            status, _, scenarios_raw = http_get("/api/scenarios")
            self.assertEqual(status, 200)
            scenarios = json.loads(scenarios_raw)
            self.assertIn("control", scenarios)
            self.assertIn("scenario2", scenarios)

            status, _, seeds_raw = http_get("/api/seeds")
            self.assertEqual(status, 200)
            self.assertGreaterEqual(len(json.loads(seeds_raw)), 6)

            # 2. Enrollment screening via HTTP POST: Control, Scenario 1, Scenario 2, Scenario 3
            s_ctrl, res_ctrl = http_post("/api/evaluate/enrollment", scenarios["control"])
            self.assertEqual(s_ctrl, 200)
            self.assertEqual(res_ctrl["layer1_decision"]["status"], "AUTO_APPROVE_ELIGIBLE")
            self.assertEqual(res_ctrl["advisory_score"]["priority_score"], 0.0)

            s_sc1, res_sc1 = http_post("/api/evaluate/enrollment", scenarios["scenario1"])
            self.assertEqual(s_sc1, 200)
            self.assertEqual(res_sc1["layer1_decision"]["status"], "GROUNDS_PENDING_HUMAN_DECISION")

            s_sc2, res_sc2 = http_post("/api/evaluate/enrollment", scenarios["scenario2"])
            self.assertEqual(s_sc2, 200)
            self.assertEqual(res_sc2["layer1_decision"]["status"], "GROUNDS_PENDING_HUMAN_DECISION")
            self.assertGreater(len(res_sc2["indirect_ownership"]), 0)

            # 3. Minimal blank-optional-fields submission must NOT inject Victor Vanish dummy data
            s_min, res_min = http_post("/api/evaluate/enrollment", {
                "legal_business_name": "E2E Minimal Clinic LLC",
                "npi": "1907770001",
                "tin": "56-7779001",
                "service_category": "FAMILY_PRACTICE",
                "formation_date": "2016-01-01",
                "application_date": "2026-02-01",
                "primary_address": None,
                "eft_account": None,
                "disclosed_persons": [],
            })
            self.assertEqual(s_min, 200)
            codes_min = {e["code"] for e in res_min["advisory_score"]["evidence_items"]}
            self.assertIn("L1-ADDR-MISSING", codes_min)
            self.assertNotIn("L1-ADDR-CMRA-PROHIBITED", codes_min)

            # 4. dry_run=True and idempotency_key over live HTTP
            _, _, audit_before_raw = http_get("/api/audit-trail")
            audit_count_before = len(json.loads(audit_before_raw))

            s_dry, res_dry = http_post("/api/evaluate/enrollment", dict(scenarios["scenario3"], dry_run=True))
            self.assertEqual(s_dry, 200)
            self.assertTrue(res_dry["dry_run"])
            self.assertEqual(res_dry["audit_record"]["record_id"], "DRY-RUN")

            _, _, audit_after_dry_raw = http_get("/api/audit-trail")
            self.assertEqual(len(json.loads(audit_after_dry_raw)), audit_count_before)

            idem_req = dict(scenarios["scenario3"], idempotency_key="HTTP-IDEM-001")
            s_i1, res_i1 = http_post("/api/evaluate/enrollment", idem_req)
            s_i2, res_i2 = http_post("/api/evaluate/enrollment", idem_req)
            self.assertEqual(s_i1, 200)
            self.assertEqual(s_i2, 200)
            self.assertEqual(res_i1["audit_record"]["record_id"], res_i2["audit_record"]["record_id"])

            # 5. Claims surveillance via HTTP POST (flagged + clean)
            s_clm_bad, res_clm_bad = http_post("/api/evaluate/claims", {
                "npi": "1900000056",
                "service_category": "BEHAVIORAL_HEALTH",
                "months_enrolled": 3,
                "total_paid_30d": 3500000,
                "max_daily_hours": 36,
                "inpatient_overlaps": 3,
                "post_mortem_claims": 2,
                "migrating_patients": 25,
            })
            self.assertEqual(s_clm_bad, 200)
            self.assertTrue(res_clm_bad["trigger_payment_suspension"])
            self.assertGreater(res_clm_bad["surveillance_priority_score"], 80.0)

            s_clm_ok, res_clm_ok = http_post("/api/evaluate/claims", {
                "npi": "1900000001",
                "service_category": "PEDIATRIC_CLINIC",
                "months_enrolled": 3,
                "total_paid_30d": 50000,
                "max_daily_hours": 8,
                "inpatient_overlaps": 0,
                "post_mortem_claims": 0,
                "migrating_patients": 0,
            })
            self.assertEqual(s_clm_ok, 200)
            self.assertFalse(res_clm_ok["trigger_payment_suspension"])

            # 6. Audit override + verify_chain via HTTP
            target_rec_id = res_i1["audit_record"]["record_id"]
            s_ov, res_ov = http_post("/api/audit/override", {
                "record_id": target_rec_id,
                "reviewer_id": "HTTP_REV_01",
                "action": "DENIED",
                "notes": "Confirmed shared terminated EFT"
            })
            self.assertEqual(s_ov, 200)
            self.assertEqual(res_ov["parent_record_id"], target_rec_id)
            self.assertTrue(res_ov["chain_valid"])

            s_ver, _, ver_raw = http_get("/api/audit/verify")
            self.assertEqual(s_ver, 200)
            self.assertTrue(json.loads(ver_raw)["chain_valid"])

            # 7. DB stats + read-only SQL guard
            s_stats, _, stats_raw = http_get("/api/db/stats")
            self.assertEqual(s_stats, 200)
            self.assertGreater(json.loads(stats_raw)["total_nodes"], 0)

            s_sql_ok, res_sql_ok = http_post("/api/db/query", {
                "sql": "SELECT COUNT(*) AS cnt FROM graph_nodes WHERE is_seed = 1"
            })
            self.assertEqual(s_sql_ok, 200)
            self.assertGreaterEqual(res_sql_ok["rows"][0]["cnt"], 6)

            s_sql_bad, res_sql_bad = http_post("/api/db/query", {"sql": "DROP TABLE graph_nodes"})
            self.assertEqual(s_sql_bad, 403)
            self.assertIn("error", res_sql_bad)
        finally:
            try:
                srv.cp1.graph_store.conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()

