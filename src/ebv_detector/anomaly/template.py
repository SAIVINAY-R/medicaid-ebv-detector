"""M4 MinHash LSH Policy Manual & Narrative Template Detection.

Detects templated policy manuals, compliance documents, and clinical narratives
frequently shared across EBV sham provider rings using MinHash and Jaccard similarity.
"""

from collections import defaultdict
import hashlib
import re
from typing import Dict, List, Optional, Set, Tuple

from ..config import ApplicationAnomalyConfig
from ..models.entities import ProviderApplication
from ..models.results import EvidenceItem, EvidenceSeverity


class MinHashTemplateDetector:
    """MinHash signature generator and template match detector."""

    def __init__(self, config: Optional[ApplicationAnomalyConfig] = None):
        self.config = config or ApplicationAnomalyConfig()
        self.num_perm = self.config.minhash_num_perm
        # Large prime for universal hashing
        self._prime = 4294967311
        
        # Fixed pseudo-random hash coefficients (a, b)
        self._hash_coeffs = [
            ((idx * 10007 + 3) % self._prime, (idx * 20011 + 7) % self._prime)
            for idx in range(1, self.num_perm + 1)
        ]
        
        # Stored signatures: app_id -> List[minhash_ints]
        self._signatures: Dict[str, List[int]] = {}
        self._app_names: Dict[str, str] = {}
        self._app_tins: Dict[str, str] = {}

    def _shingle_text(self, text: str, k: int = 3) -> Set[str]:
        """Convert text into normalized word k-shingles."""
        words = re.sub(r"[^a-zA-Z0-9\s]", " ", text).lower().split()
        if len(words) < k:
            return set(words)
        return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}

    def compute_minhash_signature(self, text: Optional[str]) -> List[int]:
        """Compute K-dimensional MinHash signature for text."""
        if not text:
            return [0] * self.num_perm
        
        shingles = self._shingle_text(text)
        if not shingles:
            return [0] * self.num_perm

        # Map each shingle string to a 32-bit integer
        shingle_hashes = [
            int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)
            for s in shingles
        ]

        # Compute minimum hash value for each permutation
        signature = []
        for a, b in self._hash_coeffs:
            min_val = min(((a * h + b) % self._prime) for h in shingle_hashes)
            signature.append(min_val)

        return signature

    def estimate_jaccard_similarity(self, sig1: List[int], sig2: List[int]) -> float:
        """Estimate Jaccard similarity as fraction of matching permutation values."""
        if len(sig1) != len(sig2) or len(sig1) == 0:
            return 0.0
        matches = sum(1 for v1, v2 in zip(sig1, sig2) if v1 == v2)
        return matches / len(sig1)

    def register_and_evaluate(self, app: ProviderApplication, record: bool = True) -> List[EvidenceItem]:
        """Evaluate application policy manual against previously submitted applications."""
        if not app.policy_manual_text or len(app.policy_manual_text.strip()) < 50:
            return []

        target_sig = self.compute_minhash_signature(app.policy_manual_text)
        target_tin = re.sub(r"\D", "", app.tin or "")
        evidence: List[EvidenceItem] = []

        matching_apps = []
        max_sim = 0.0
        best_match_id = None
        best_match_name = None

        # Compare against existing signatures (exclude same-TIN corporate multi-site sharing)
        for prior_app_id, prior_sig in self._signatures.items():
            if prior_app_id == app.application_id:
                continue
            prior_tin = self._app_tins.get(prior_app_id, "")
            if target_tin and prior_tin and target_tin == prior_tin:
                continue

            j_sim = self.estimate_jaccard_similarity(target_sig, prior_sig)
            if j_sim >= self.config.template_similarity_threshold:
                prior_name = self._app_names.get(prior_app_id, prior_app_id)
                matching_apps.append((prior_app_id, prior_name, j_sim))
                if j_sim > max_sim:
                    max_sim = j_sim
                    best_match_id = prior_app_id
                    best_match_name = prior_name

        if matching_apps:
            evidence.append(EvidenceItem(
                code="M4-TEMPLATE-POLICY-CLONE",
                description=(
                    f"Policy manual has {max_sim:.1%} text similarity with '{best_match_name}' "
                    f"({len(matching_apps)} matching application(s) detected). Templated document ring pattern."
                ),
                source_layer="M4_ANOMALY",
                severity=EvidenceSeverity.HIGH,
                verified_fact=False,
                fact_key="TEMPLATE:POLICY_CLONE",
                family="SUBMISSION",
                details={
                    "strongest_match_app_id": best_match_id,
                    "strongest_match_name": best_match_name,
                    "max_jaccard_similarity": round(max_sim, 4),
                    "total_matching_applications": len(matching_apps),
                    "matched_applications": [
                        {"app_id": aid, "name": aname, "similarity": round(s, 4)}
                        for aid, aname, s in matching_apps
                    ]
                }
            ))

        # Store signature for future comparisons unless evaluating in dry-run/read-only mode
        if record:
            self._signatures[app.application_id] = target_sig
            self._app_names[app.application_id] = app.legal_business_name
            self._app_tins[app.application_id] = target_tin

        return evidence

