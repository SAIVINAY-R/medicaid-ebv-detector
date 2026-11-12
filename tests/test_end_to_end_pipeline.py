"""Integration tests verifying end-to-end Checkpoint 1 and Checkpoint 2 pipeline execution."""

import unittest

from ebv_detector.evaluation.harness import EvaluationHarness
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

        # Verify all specific scenarios passed
        for scen, detected in report.scenario_detection_rates.items():
            self.assertTrue(detected, f"Scenario failed detection: {scen}")


if __name__ == "__main__":
    unittest.main()

