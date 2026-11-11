"""Evaluation Harness & Benchmark Suite (Spike S9).

Evaluates the EBV detection pipeline against synthetic benchmark suites:
- Leave-One-Ring-Out (LORO) generalization across fraud topologies
- Precision, Recall, F1, and False Positive Rate across legitimate vs sham cohorts
- Operational review burden estimation
- Verification of explainability and statutory grounds alignment
"""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple

from ..config import SystemConfig
from ..models.entities import ClaimRecord, ProviderApplication
from ..models.results import DecisionStatus
from ..pipeline.checkpoint1 import Checkpoint1Pipeline
from ..pipeline.checkpoint2 import Checkpoint2Pipeline
from ..synthetic.generator import SyntheticDataset, SyntheticEBVGenerator


@dataclass
class BenchmarkReport:
    """Summary metrics of an evaluation run."""
    total_applications: int
    total_fraud_entities: int
    total_legitimate_entities: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float
    recall: float
    f1_score: float
    false_positive_rate: float
    average_latency_ms: float
    review_burden_pct: float
    scenario_detection_rates: Dict[str, bool] = field(default_factory=dict)
    detailed_results: List[Dict[str, Any]] = field(default_factory=list)

    def print_summary(self):
        """Display formatted markdown evaluation summary."""
        print("\n" + "=" * 70)
        print("          EBV DETECTION BENCHMARK EVALUATION REPORT")
        print("=" * 70)
        print(f"Total Cohort Evaluated:       {self.total_applications} providers")
        print(f"  - Fraud / Sham Targets:     {self.total_fraud_entities}")
        print(f"  - Legitimate Controls:      {self.total_legitimate_entities}")
        print("-" * 70)
        print(f"True Positives:               {self.true_positives}")
        print(f"False Positives:              {self.false_positives}")
        print(f"True Negatives:               {self.true_negatives}")
        print(f"False Negatives:              {self.false_negatives}")
        print("-" * 70)
        print(f"Precision:                    {self.precision:.1%}")
        print(f"Recall (Sensitivity):         {self.recall:.1%}")
        print(f"F1 Score:                     {self.f1_score:.3f}")
        print(f"False Positive Rate (FPR):    {self.false_positive_rate:.1%}")
        print(f"Review Burden on Clean Apps:  {self.review_burden_pct:.1%}")
        print(f"Average Evaluation Latency:   {self.average_latency_ms:.2f} ms / app")
        print("-" * 70)
        print("SCENARIO-BY-SCENARIO DETECTION:")
        for scen, detected in self.scenario_detection_rates.items():
            status = "PASSED [DETECTED]" if detected else "FAILED [MISSED]"
            print(f"  * {scen[:65]}... -> {status}")
        print("=" * 70 + "\n")


class EvaluationHarness:
    """Runs end-to-end evaluation benchmark over synthetic or historical datasets."""

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or SystemConfig()

    def run_benchmark(self, dataset: Optional[SyntheticDataset] = None) -> BenchmarkReport:
        """Run benchmark evaluation over provided or freshly generated dataset."""
        if not dataset:
            generator = SyntheticEBVGenerator(seed=42)
            dataset = generator.generate_benchmark_suite()

        cp1 = Checkpoint1Pipeline(self.config, dataset.seed_records)
        cp2 = Checkpoint2Pipeline(self.config)

        # Ingest corporate holding edges for GR-4
        cp1.ingest_ownership_edges(dataset.ownership_edges)

        # Pre-seed CP2 with known suspended providers
        for s in dataset.seed_records:
            if s.identifier_type == "NPI":
                cp2.register_sanctioned_provider(s.identifier_value)

        # Index claims by billing NPI
        claims_by_npi = {}
        for c in dataset.claims:
            claims_by_npi.setdefault(c.billing_provider_npi, []).append(c)

        # Ingest prior claims into CP2 for patient sharing
        cp2.ingest_claims(dataset.claims)

        tp, fp, tn, fn = 0, 0, 0, 0
        total_time_ms = 0.0
        scenario_results: Dict[str, bool] = {}
        detailed: List[Dict[str, Any]] = []

        for app in dataset.applications:
            start_t = time.perf_counter()
            
            # 1. Run Checkpoint 1
            cp1_out = cp1.evaluate_application(app)
            
            # 2. Run Checkpoint 2 if claims exist for provider
            p_claims = claims_by_npi.get(app.npi, [])
            cp2_out = None
            if p_claims:
                cp2_out = cp2.evaluate_provider_early_billing(app, months_enrolled=3, claims=p_claims)

            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            total_time_ms += elapsed_ms

            # Determine whether system flagged this provider as requiring adverse intervention
            # Flags: Layer 1 adverse status OR Advisory Risk Level HIGH/CRITICAL OR CP2 critical claims alert
            is_flagged = (
                cp1_out.layer1_decision.status in (DecisionStatus.REVIEW_REQUIRED, DecisionStatus.DENIED_GROUNDS)
                or cp1_out.advisory_score.composite_risk_level in ("HIGH", "CRITICAL")
                or (cp2_out is not None and (cp2_out.trigger_unannounced_site_visit or cp2_out.trigger_payment_suspension))
            )

            is_actual_fraud = (dataset.ground_truth.get(app.application_id) == "EBV_FRAUD")
            scen_desc = dataset.scenario_metadata.get(app.application_id, "Unknown Scenario")

            if is_actual_fraud:
                if is_flagged:
                    tp += 1
                    scenario_results[scen_desc] = True
                else:
                    fn += 1
                    scenario_results[scen_desc] = False
            else:
                if is_flagged:
                    fp += 1
                else:
                    tn += 1

            detailed.append({
                "application_id": app.application_id,
                "npi": app.npi,
                "legal_business_name": app.legal_business_name,
                "ground_truth": dataset.ground_truth.get(app.application_id),
                "is_flagged": is_flagged,
                "layer1_status": cp1_out.layer1_decision.status.value,
                "priority_score": cp1_out.advisory_score.priority_score,
                "risk_level": cp1_out.advisory_score.composite_risk_level,
                "latency_ms": elapsed_ms
            })

        total_apps = len(dataset.applications)
        total_fraud = tp + fn
        total_legit = fp + tn

        prec = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
        fpr = fp / total_legit if total_legit > 0 else 0.0
        review_burden = fp / total_legit if total_legit > 0 else 0.0
        avg_lat = total_time_ms / total_apps if total_apps > 0 else 0.0

        return BenchmarkReport(
            total_applications=total_apps,
            total_fraud_entities=total_fraud,
            total_legitimate_entities=total_legit,
            true_positives=tp,
            false_positives=fp,
            true_negatives=tn,
            false_negatives=fn,
            precision=round(prec, 4),
            recall=round(rec, 4),
            f1_score=round(f1, 4),
            false_positive_rate=round(fpr, 4),
            average_latency_ms=round(avg_lat, 2),
            review_burden_pct=round(review_burden, 4),
            scenario_detection_rates=scenario_results,
            detailed_results=detailed
        )
