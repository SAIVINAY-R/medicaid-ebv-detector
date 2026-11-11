"""Checkpoint 1 Enrollment & Maintenance Pipeline.

Integrates:
- M1 Fellegi-Sunter Entity Resolution & Near-Match Queue
- M2 Address Hygiene, DATA-039 Prohibitions, and Density Clustering
- M3 Relationship Graph Recursive CTE, GR-4 Indirect Ownership, and Seed Risk Propagation
- M4 Submission Velocity and MinHash Template Detection
- Layer 1 Deterministic Screening Gate (42 CFR 455.416 & 455.450)
- Layer 2 Assistive Scorer & Reviewer Evidence Panel
- WORM Cryptographic Audit Logging
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..address.clustering import AddressClusterEngine
from ..anomaly.application import ApplicationVelocityTracker
from ..anomaly.template import MinHashTemplateDetector
from ..config import SystemConfig
from ..entity_resolution.fellegi_sunter import FellegiSunterResolver
from ..entity_resolution.near_match_queue import NearMatchQueue
from ..graph.risk_propagation import SeedRiskPropagator
from ..graph.schema import EdgeType, GraphEdge, GraphNode, NodeType
from ..graph.store import GraphStore
from ..graph.supernodes import SupernodeManager
from ..graph.traversal import GraphTraversalEngine, IndirectOwnershipResult, PathResult
from ..models.entities import OwnershipEdge, ProviderApplication, SeedRecord
from ..models.results import AdvisoryScore, AuditRecord, EvidenceItem, EvidenceSeverity, Layer1Decision
from .audit import WormAuditLogger
from .layer1_rules import Layer1RulesEngine
from .layer2_advisory import Layer2AdvisoryEngine


@dataclass
class Checkpoint1EvaluationPackage:
    """Complete unified output of Checkpoint 1 processing."""
    application_id: str
    layer1_decision: Layer1Decision
    advisory_score: AdvisoryScore
    audit_record: AuditRecord
    graph_paths_to_seeds: List[PathResult] = field(default_factory=list)
    indirect_ownership: List[IndirectOwnershipResult] = field(default_factory=list)
    near_matches: List[Any] = field(default_factory=list)


class Checkpoint1Pipeline:
    """Full-featured enrollment checkpoint engine for CPMM."""

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        seeds: Optional[List[SeedRecord]] = None
    ):
        self.config = config or SystemConfig()
        self.seeds = seeds or []
        
        # Subsystems
        self.graph_store = GraphStore()
        self.supernode_mgr = SupernodeManager(
            self.graph_store,
            degree_threshold=self.config.graph.supernode_degree_threshold,
            damping_factor=self.config.graph.supernode_damping_factor
        )
        self.graph_traversal = GraphTraversalEngine(self.graph_store, max_hops=self.config.graph.max_traversal_hops)
        self.seed_propagator = SeedRiskPropagator(
            self.graph_store,
            self.supernode_mgr,
            restart_prob=self.config.graph.ppr_restart_prob,
            max_iterations=self.config.graph.ppr_max_iterations
        )
        
        self.resolver = FellegiSunterResolver(self.config.entity_resolution)
        self.near_match_queue = NearMatchQueue()
        self.address_cluster_engine = AddressClusterEngine(self.config.address)
        self.velocity_tracker = ApplicationVelocityTracker(self.config.anomaly)
        self.template_detector = MinHashTemplateDetector(self.config.anomaly)
        
        self.layer1_engine = Layer1RulesEngine(self.seeds, self.config)
        self.layer2_engine = Layer2AdvisoryEngine()
        self.audit_logger = WormAuditLogger(self.config.model_version)
        
        # Internal application cohort
        self._application_cohort: List[ProviderApplication] = []
        
        # Ingest initial seeds into graph store
        self._ingest_seeds_into_graph()

    def _ingest_seeds_into_graph(self):
        """Register all known-bad seed nodes in graph store."""
        for s in self.seeds:
            # Map directly to entity identifier value if it's an entity key, otherwise prefix
            node_id = s.identifier_value if s.identifier_type in ("PERSON_NAME", "NPI", "TIN") else f"SEED-{s.identifier_type}-{s.identifier_value}"
            self.graph_store.add_node(GraphNode(
                node_id=node_id,
                node_type=NodeType.SEED,
                label=f"Seed: {s.identifier_type}={s.identifier_value}",
                is_seed=True,
                seed_source=s.source,
                attributes={"reason": s.reason, "identifier_type": s.identifier_type, "value": s.identifier_value}
            ))

    def ingest_ownership_edges(self, edges: List[OwnershipEdge]):
        """Ingest corporate holding company hierarchy for GR-4 indirect ownership."""
        for e in edges:
            # Check if parent is a seed
            is_parent_seed = any(s.identifier_value == e.parent_entity_id for s in self.seeds)
            seed_source = next((s.source for s in self.seeds if s.identifier_value == e.parent_entity_id), None)
            seed_reason = next((s.reason for s in self.seeds if s.identifier_value == e.parent_entity_id), None)

            existing_parent = self.graph_store.get_node(e.parent_entity_id)
            if not existing_parent:
                self.graph_store.add_node(GraphNode(
                    node_id=e.parent_entity_id,
                    node_type=NodeType.SEED if is_parent_seed else NodeType.ORGANIZATION,
                    label=f"Org: {e.parent_entity_id}",
                    is_seed=is_parent_seed,
                    seed_source=seed_source,
                    attributes={"reason": seed_reason} if seed_reason else {}
                ))
            elif is_parent_seed and not existing_parent.is_seed:
                existing_parent.is_seed = True
                existing_parent.seed_source = seed_source
                existing_parent.attributes["reason"] = seed_reason
                self.graph_store.add_node(existing_parent)

            if not self.graph_store.get_node(e.child_entity_id):
                self.graph_store.add_node(GraphNode(
                    node_id=e.child_entity_id,
                    node_type=NodeType.PROVIDER,
                    label=f"Org: {e.child_entity_id}"
                ))
            # Direct ownership edge
            self.graph_store.add_edge(GraphEdge(
                source_id=e.parent_entity_id,
                target_id=e.child_entity_id,
                edge_type=EdgeType.DIRECT_OWNERSHIP,
                ownership_percentage=e.ownership_percentage,
                effective_start=e.effective_date,
                effective_end=e.end_date
            ))

    def _sync_application_to_graph(self, app: ProviderApplication):
        """Map provider application entities and identifiers into graph store."""
        p_node_id = app.npi
        self.graph_store.add_node(GraphNode(
            node_id=p_node_id,
            node_type=NodeType.PROVIDER,
            label=app.legal_business_name,
            attributes={"npi": app.npi, "tin": app.tin, "service_category": app.service_category}
        ))

        # Primary Address
        if app.primary_address:
            addr_id = f"ADDR-{app.primary_address.normalized_single_line}"
            self.graph_store.add_node(GraphNode(
                node_id=addr_id,
                node_type=NodeType.ADDRESS,
                label=app.primary_address.address_line1,
                attributes={"zip5": app.primary_address.zip5, "is_cmra": app.primary_address.is_cmra}
            ))
            self.graph_store.add_edge(GraphEdge(
                source_id=p_node_id,
                target_id=addr_id,
                edge_type=EdgeType.USES_ADDRESS
            ))
            # Check if this address matches any known address seed
            norm_addr = app.primary_address.normalized_single_line
            for s in self.seeds:
                if s.identifier_type == "ADDRESS" and s.identifier_value.upper() == norm_addr:
                    seed_node_id = f"SEED-{s.identifier_type}-{s.identifier_value}"
                    self.graph_store.add_edge(GraphEdge(
                        source_id=addr_id,
                        target_id=seed_node_id,
                        edge_type=EdgeType.LINKED_SEED
                    ))

        # EFT Account
        if app.eft_account:
            eft_id = f"EFT-{app.eft_account.account_key}"
            self.graph_store.add_node(GraphNode(
                node_id=eft_id,
                node_type=NodeType.EFT_ACCOUNT,
                label=f"EFT:{app.eft_account.routing_number}"
            ))
            self.graph_store.add_edge(GraphEdge(
                source_id=p_node_id,
                target_id=eft_id,
                edge_type=EdgeType.USES_EFT
            ))
            # Check seed link
            for s in self.seeds:
                if s.identifier_type == "EFT" and s.identifier_value == app.eft_account.account_key:
                    seed_node_id = f"SEED-{s.identifier_type}-{s.identifier_value}"
                    self.graph_store.add_edge(GraphEdge(
                        source_id=eft_id,
                        target_id=seed_node_id,
                        edge_type=EdgeType.LINKED_SEED
                    ))

        # Phone
        if app.phone:
            phone_id = f"PHONE-{app.phone}"
            self.graph_store.add_node(GraphNode(
                node_id=phone_id,
                node_type=NodeType.PHONE,
                label=app.phone
            ))
            self.graph_store.add_edge(GraphEdge(
                source_id=p_node_id,
                target_id=phone_id,
                edge_type=EdgeType.USES_PHONE
            ))

        # Device
        if app.device_fingerprint_id:
            dev_id = f"DEV-{app.device_fingerprint_id}"
            self.graph_store.add_node(GraphNode(
                node_id=dev_id,
                node_type=NodeType.DEVICE,
                label=app.device_fingerprint_id
            ))
            self.graph_store.add_edge(GraphEdge(
                source_id=p_node_id,
                target_id=dev_id,
                edge_type=EdgeType.SUBMITTED_FROM
            ))

        # Disclosed Persons
        for person in app.disclosed_persons:
            person_id = f"PERSON-{person.ssn_dob_key}"
            self.graph_store.add_node(GraphNode(
                node_id=person_id,
                node_type=NodeType.PERSON,
                label=person.full_name,
                attributes={"role": person.role, "ssn_last4": person.ssn_last4}
            ))
            # Ownership or role edge
            if person.direct_ownership_pct > 0.0:
                self.graph_store.add_edge(GraphEdge(
                    source_id=person_id,
                    target_id=p_node_id,
                    edge_type=EdgeType.DIRECT_OWNERSHIP,
                    ownership_percentage=person.direct_ownership_pct
                ))
            else:
                self.graph_store.add_edge(GraphEdge(
                    source_id=person_id,
                    target_id=p_node_id,
                    edge_type=EdgeType.MANAGING_EMPLOYEE
                ))
            # Seed link
            for s in self.seeds:
                if s.identifier_type == "SSN4_DOB" and s.identifier_value == person.ssn_dob_key:
                    seed_node_id = f"SEED-{s.identifier_type}-{s.identifier_value}"
                    self.graph_store.add_edge(GraphEdge(
                        source_id=person_id,
                        target_id=seed_node_id,
                        edge_type=EdgeType.LINKED_SEED
                    ))

        # Clinicians
        for clin_npi in app.rendering_clinician_npis:
            c_id = f"CLINICIAN-{clin_npi}"
            self.graph_store.add_node(GraphNode(
                node_id=c_id,
                node_type=NodeType.PERSON,
                label=f"Clinician NPI {clin_npi}"
            ))
            self.graph_store.add_edge(GraphEdge(
                source_id=c_id,
                target_id=p_node_id,
                edge_type=EdgeType.CLINICIAN
            ))

    def evaluate_application(self, app: ProviderApplication) -> Checkpoint1EvaluationPackage:
        """Run entire end-to-end Checkpoint 1 evaluation pipeline on application."""
        collected_evidence: List[EvidenceItem] = []

        # 1. Synchronize to Graph
        self._sync_application_to_graph(app)

        # 2. M1 Entity Resolution against cohort
        matches = self.resolver.find_matches_in_cohort(app, self._application_cohort)
        near_matches = []
        for m in matches:
            if m.is_match:
                collected_evidence.append(EvidenceItem(
                    code="M1-RESOLVER-HIGH-CONFIDENCE-LINK",
                    description=(
                        f"M1 Entity Resolution matched application with existing provider '{m.candidate_id_b}' "
                        f"(Fellegi-Sunter score: {m.match_score:.1f}, posterior prob: {m.posterior_prob:.1%})."
                    ),
                    source_layer="M1_RESOLVER",
                    severity=EvidenceSeverity.HIGH,
                    verified_fact=True,
                    details=m.field_level_comparisons
                ))
            elif m.is_near_match:
                q_item = self.near_match_queue.enqueue(m)
                near_matches.append(q_item)
                collected_evidence.append(EvidenceItem(
                    code="M1-RESOLVER-NEAR-MATCH-ENQUEUED",
                    description=(
                        f"Near-match candidate found with '{m.candidate_id_b}' (Score: {m.match_score:.1f}). "
                        "Enqueued to human reviewer for adjudication (DATA-025)."
                    ),
                    source_layer="M1_RESOLVER",
                    severity=EvidenceSeverity.MEDIUM,
                    verified_fact=False,
                    details={"queue_item_id": q_item.item_id, "score": m.match_score}
                ))

        # 3. M2 Address Risk and Co-Location Clustering
        density_evidence = self.address_cluster_engine.evaluate_density(app)
        collected_evidence.extend(density_evidence)
        self.address_cluster_engine.register_provider(app)

        # 4. M3 Graph Traversal: Paths to Seeds & GR-4 Indirect Ownership
        paths_to_seeds = self.graph_traversal.find_paths_to_seeds(app.npi)
        for p in paths_to_seeds:
            collected_evidence.append(EvidenceItem(
                code="M3-GRAPH-PATH-TO-SANCTIONED-SEED",
                description=(
                    f"Relationship graph discovered {p.hops}-hop path to sanctioned seed '{p.target_node_id}' "
                    f"({p.target_seed_source}: {p.target_seed_reason}). Path: {p.path_description}."
                ),
                source_layer="M3_GRAPH",
                severity=EvidenceSeverity.CRITICAL if p.hops <= 2 else EvidenceSeverity.HIGH,
                verified_fact=True,
                details={"hops": p.hops, "seed_source": p.target_seed_source, "path": p.path_nodes}
            ))

        indirect_ownership = self.graph_traversal.calculate_gr4_indirect_ownership(app.npi)

        # 4b. Personalized PageRank Risk Propagation
        ppr_scores = self.seed_propagator.compute_personalized_pagerank()
        ppr_score = self.seed_propagator.get_risk_score(app.npi, ppr_scores)
        if ppr_score >= self.config.graph.ppr_alert_threshold:
            collected_evidence.append(EvidenceItem(
                code="M3-PPR-SEED-PROXIMITY-ALERT",
                description=(
                    f"Personalized PageRank seed risk propagation score is {ppr_score:.2f} "
                    f"(alert threshold >= {self.config.graph.ppr_alert_threshold}). High topological proximity to fraud rings."
                ),
                source_layer="M3_GRAPH",
                severity=EvidenceSeverity.HIGH,
                verified_fact=False,
                details={"ppr_score": ppr_score}
            ))

        # 5. M4 Velocity & Template Detection
        velocity_evidence = self.velocity_tracker.record_and_evaluate(app)
        collected_evidence.extend(velocity_evidence)

        template_evidence = self.template_detector.register_and_evaluate(app)
        collected_evidence.extend(template_evidence)

        # 6. Layer 1 Authoritative Rules Gate
        layer1_decision = self.layer1_engine.evaluate_application(
            app=app,
            graph_seed_paths=paths_to_seeds,
            indirect_ownership_results=indirect_ownership
        )

        # 7. Layer 2 Assistive Advisory Scorer
        combined_evidence = collected_evidence + layer1_decision.verified_facts
        advisory_score = self.layer2_engine.compute_advisory_package(
            app=app,
            layer1_decision=layer1_decision,
            evidence_items=combined_evidence,
            ppr_risk_score=ppr_score
        )

        # 8. WORM Audit Trail
        audit_rec = self.audit_logger.log_decision_event(
            app=app,
            layer1_decision=layer1_decision,
            advisory_score=advisory_score
        )

        # Add application to internal cohort for future comparisons
        self._application_cohort.append(app)

        return Checkpoint1EvaluationPackage(
            application_id=app.application_id,
            layer1_decision=layer1_decision,
            advisory_score=advisory_score,
            audit_record=audit_rec,
            graph_paths_to_seeds=paths_to_seeds,
            indirect_ownership=indirect_ownership,
            near_matches=near_matches
        )
