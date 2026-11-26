"""Fellegi-Sunter Probabilistic Record Linkage Engine (M1).

Implements classic mathematical record linkage theory:
- Log-likelihood ratio weights: w_agree = log2(m / u), w_disagree = log2((1-m) / (1-u))
- Partial agreement interpolation using Jaro-Winkler and Levenshtein comparators
- Multi-pass blocking (Exact NPI, TIN, SSN4+DOB, Phone, EFT, Address+Zip)
- Near-Match Queue triaging (Auto-link, Reviewer Queue, Non-match)
- Strict compliance with NC RFP Attachment C §3(e)(ii) (rule-calibrated m/u priors)
"""

from collections import defaultdict
from dataclasses import dataclass, field
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..config import EntityResolutionConfig
from ..models.entities import AddressRecord, PersonDisclosure, ProviderApplication
from ..models.results import MatchResult
from .comparators import (
    clean_phone,
    double_metaphone,
    jaro_winkler_similarity,
    levenshtein_similarity,
    normalize_string,
    token_set_similarity,
)


@dataclass
class FieldWeightConfig:
    """Fellegi-Sunter prior probabilities for a specific field."""
    m_prob: float  # P(agreement | match), usually high (0.85 - 0.99)
    u_prob: float  # P(agreement | non-match), usually low (0.0001 - 0.05)

    @property
    def agree_weight(self) -> float:
        """w_agree = log2(m / u)"""
        return math.log2(self.m_prob / self.u_prob)

    @property
    def disagree_weight(self) -> float:
        """w_disagree = log2((1 - m) / (1 - u))"""
        m_comp = max(1e-6, 1.0 - self.m_prob)
        u_comp = max(1e-6, 1.0 - self.u_prob)
        return math.log2(m_comp / u_comp)

    def calculate_weight(self, similarity: float, high_thresh: float = 0.90, med_thresh: float = 0.75) -> float:
        """Calculate weighted score allowing partial agreement interpolation."""
        if similarity >= high_thresh:
            return self.agree_weight
        elif similarity >= med_thresh:
            # Linear interpolation between disagree and agree weight
            ratio = (similarity - med_thresh) / (high_thresh - med_thresh)
            return self.disagree_weight + ratio * (self.agree_weight - self.disagree_weight)
        else:
            return self.disagree_weight


class FellegiSunterResolver:
    """Probabilistic entity resolver for Medicaid applications and historical records."""

    def __init__(self, config: Optional[EntityResolutionConfig] = None):
        self.config = config or EntityResolutionConfig()
        
        # Domain-calibrated Fellegi-Sunter weights
        # High-discriminating identifiers have very low u_prob (huge agreement weight)
        self.field_weights: Dict[str, FieldWeightConfig] = {
            "ssn_last4_dob": FieldWeightConfig(m_prob=0.98, u_prob=0.00005), # ~14.2 bits
            "eft_account": FieldWeightConfig(m_prob=0.95, u_prob=0.0001),     # ~13.2 bits
            "npi": FieldWeightConfig(m_prob=0.99, u_prob=0.00001),             # ~16.6 bits
            "tin": FieldWeightConfig(m_prob=0.97, u_prob=0.00005),             # ~14.2 bits
            "phone": FieldWeightConfig(m_prob=0.88, u_prob=0.001),             # ~9.8 bits
            "legal_name": FieldWeightConfig(m_prob=0.92, u_prob=0.002),        # ~8.8 bits
            "address": FieldWeightConfig(m_prob=0.85, u_prob=0.005),           # ~7.4 bits
            "device_id": FieldWeightConfig(m_prob=0.90, u_prob=0.0002),        # ~12.1 bits
            "disclosed_person": FieldWeightConfig(m_prob=0.85, u_prob=0.005),  # ~7.4 bits
        }
        
        # Prior probability of two random candidates being the same entity
        self.prior_match_prob: float = 0.001

    def generate_blocking_keys(self, app: ProviderApplication) -> Dict[str, List[str]]:
        """Extract multi-pass blocking keys for candidate generation."""
        keys = defaultdict(list)
        
        # Key 1: Direct NPI & TIN
        if app.npi:
            keys["npi"].append(app.npi.strip())
        if app.tin:
            keys["tin"].append(re.sub(r"\D", "", app.tin))
            
        # Key 2: Disclosed SSN4 + DOB
        for person in app.disclosed_persons:
            if person.ssn_last4 and person.date_of_birth:
                keys["ssn_last4_dob"].append(f"{person.ssn_last4}_{person.date_of_birth}")
            # Key 2b: Name double metaphone
            p_code, s_code = double_metaphone(person.last_name)
            if p_code:
                keys["person_phonetic"].append(f"{p_code}_{person.first_name[:2].upper()}")

        # Key 3: Clean 10-digit Phone
        phone_c = clean_phone(app.phone)
        if phone_c:
            keys["phone_clean"].append(phone_c)

        # Key 4: EFT Routing + Account
        if app.eft_account and app.eft_account.account_number:
            keys["eft_routing_account"].append(app.eft_account.account_key)

        # Key 5: Normalized Address + Zip5
        if app.primary_address:
            addr = app.primary_address
            first_word = normalize_string(addr.address_line1).split()
            first_term = first_word[0] if first_word else ""
            if addr.zip5 and first_term:
                keys["address_norm_zip"].append(f"{addr.zip5}_{first_term}")

        # Key 6: Device Fingerprint
        if app.device_fingerprint_id:
            keys["device_id"].append(app.device_fingerprint_id)

        return keys

    def compare_pair(
        self, app_a: ProviderApplication, app_b: ProviderApplication, matched_blocking_key: str
    ) -> MatchResult:
        """Compute field-level similarities, Fellegi-Sunter log-weight, and match status."""
        field_sims: Dict[str, float] = {}
        total_weight = 0.0
        positive_weight = 0.0

        def add_weight(w: float) -> None:
            nonlocal total_weight, positive_weight
            total_weight += w
            if w > 0:
                positive_weight += w

        # 1. Compare Legal / DBA Names
        names_a = [n for n in (app_a.legal_business_name, app_a.dba_name) if n]
        names_b = [n for n in (app_b.legal_business_name, app_b.dba_name) if n]
        name_sim = max(
            (
                max(jaro_winkler_similarity(na, nb), token_set_similarity(na, nb))
                for na in names_a
                for nb in names_b
            ),
            default=0.0,
        )
        field_sims["legal_name"] = name_sim
        add_weight(self.field_weights["legal_name"].calculate_weight(
            name_sim, self.config.jaro_winkler_high, self.config.jaro_winkler_med
        ))

        # 2. Compare Addresses
        if app_a.primary_address and app_b.primary_address:
            addr_a = app_a.primary_address.normalized_single_line
            addr_b = app_b.primary_address.normalized_single_line
            addr_sim = max(
                jaro_winkler_similarity(addr_a, addr_b),
                levenshtein_similarity(addr_a, addr_b)
            )
            field_sims["address"] = addr_sim
            add_weight(self.field_weights["address"].calculate_weight(addr_sim, 0.88, 0.70))

        # 3. Compare Phones
        phone_a = clean_phone(app_a.phone)
        phone_b = clean_phone(app_b.phone)
        if phone_a and phone_b:
            phone_sim = 1.0 if phone_a == phone_b else 0.0
            field_sims["phone"] = phone_sim
            add_weight(self.field_weights["phone"].calculate_weight(phone_sim, 0.99, 0.50))

        # 4. Compare EFT Accounts
        eft_a_valid = bool(app_a.eft_account and app_a.eft_account.routing_number and app_a.eft_account.account_number)
        eft_b_valid = bool(app_b.eft_account and app_b.eft_account.routing_number and app_b.eft_account.account_number)
        if eft_a_valid and eft_b_valid:
            eft_match = 1.0 if app_a.eft_account.account_key == app_b.eft_account.account_key else 0.0
            field_sims["eft_account"] = eft_match
            add_weight(self.field_weights["eft_account"].calculate_weight(eft_match, 0.99, 0.50))

        # 5. Compare Disclosed Persons (SSN4 + DOB or Name)
        max_person_sim = 0.0
        exact_ssn4_dob_match = False
        for p_a in app_a.disclosed_persons:
            for p_b in app_b.disclosed_persons:
                if p_a.ssn_dob_key == p_b.ssn_dob_key and len(p_a.ssn_last4) == 4:
                    exact_ssn4_dob_match = True
                p_sim = jaro_winkler_similarity(p_a.full_name, p_b.full_name)
                if p_sim > max_person_sim:
                    max_person_sim = p_sim

        if exact_ssn4_dob_match:
            field_sims["ssn_last4_dob"] = 1.0
            add_weight(self.field_weights["ssn_last4_dob"].agree_weight)
        elif max_person_sim > 0.0:
            field_sims["disclosed_person_name"] = max_person_sim
            add_weight(self.field_weights["disclosed_person"].calculate_weight(
                max_person_sim, 0.90, 0.75
            ))

        # 6. Compare Submission Device Fingerprint
        if app_a.device_fingerprint_id and app_b.device_fingerprint_id:
            dev_match = 1.0 if app_a.device_fingerprint_id == app_b.device_fingerprint_id else 0.0
            field_sims["device_id"] = dev_match
            add_weight(self.field_weights["device_id"].calculate_weight(dev_match, 0.99, 0.50))

        # 7. Compare NPI
        npi_match = None
        if app_a.npi and app_b.npi:
            npi_match = 1.0 if app_a.npi.strip() == app_b.npi.strip() else 0.0
            field_sims["npi"] = npi_match
            add_weight(self.field_weights["npi"].calculate_weight(npi_match, 0.99, 0.50))

        # 8. Compare TIN
        tin_match = None
        clean_tin_a = re.sub(r"\D", "", app_a.tin) if app_a.tin else ""
        clean_tin_b = re.sub(r"\D", "", app_b.tin) if app_b.tin else ""
        if clean_tin_a and clean_tin_b:
            tin_match = 1.0 if clean_tin_a == clean_tin_b else 0.0
            field_sims["tin"] = tin_match
            add_weight(self.field_weights["tin"].calculate_weight(tin_match, 0.99, 0.50))

        # Hard conflict & strong agreement checks:
        hard_conflict = False
        if npi_match == 0.0 or tin_match == 0.0:
            hard_conflict = True

        strong_agreements = 0
        if npi_match == 1.0:
            strong_agreements += 1
        if tin_match == 1.0:
            strong_agreements += 1
        if app_a.eft_account and app_b.eft_account and field_sims.get("eft_account") == 1.0:
            strong_agreements += 1
        if exact_ssn4_dob_match:
            strong_agreements += 1

        num_fields_compared = len(field_sims)

        # Calibrated Bayes posterior probability (clamped exponent to prevent float64 overflow)
        # P(M|gamma) = P(M) * 2^W / [ P(M) * 2^W + (1 - P(M)) ]
        clamped_w = max(-100.0, min(100.0, total_weight))
        odds = (self.prior_match_prob / (1.0 - self.prior_match_prob)) * (2.0 ** clamped_w)
        posterior_prob = min(0.9999, odds / (1.0 + odds))

        # Determine match vs near match vs non match
        # Auto-link requires total weight >= match threshold, no hard identifier conflict,
        # and at least two agreeing strong identifiers (NPI, TIN, EFT, SSN4+DOB).
        has_auto_link = (
            total_weight >= self.config.match_threshold
            and not hard_conflict
            and strong_agreements >= 2
        )
        
        # Demote to near-match queue if candidate has high agreement weight but carries
        # hard conflicts or insufficient strong identifiers, or if total weight is in near-match range.
        has_near_match = (
            not has_auto_link
            and (
                (self.config.near_match_threshold <= total_weight)
                or (positive_weight >= self.config.match_threshold and (hard_conflict or strong_agreements < 2))
            )
        )

        is_match = has_auto_link
        is_near_match = has_near_match

        action = "DO_NOT_LINK"
        if is_match:
            action = "AUTO_LINK"
            explanation = (
                f"Auto-linked: {strong_agreements} strong identifiers agreed, "
                f"weight={total_weight:.2f} >= {self.config.match_threshold} across {num_fields_compared} compared fields."
            )
        elif is_near_match:
            action = "SEND_TO_QUEUE"
            if hard_conflict:
                explanation = (
                    f"Sent to near-match queue: Hard identifier conflict detected (differing NPI/TIN), "
                    f"weight={total_weight:.2f} across {num_fields_compared} compared fields."
                )
            elif strong_agreements < 2:
                explanation = (
                    f"Sent to near-match queue: Requires >=2 agreeing strong identifiers for auto-link ({strong_agreements} found), "
                    f"weight={total_weight:.2f} across {num_fields_compared} compared fields."
                )
            else:
                explanation = (
                    f"Sent to near-match queue: Near-match weight={total_weight:.2f} across {num_fields_compared} compared fields."
                )
        else:
            explanation = f"Do not link: weight={total_weight:.2f} < {self.config.near_match_threshold} across {num_fields_compared} compared fields."

        return MatchResult(
            candidate_id_a=app_a.application_id,
            candidate_id_b=app_b.application_id,
            match_score=round(total_weight, 2),
            posterior_prob=round(posterior_prob, 4),
            is_match=is_match,
            is_near_match=is_near_match,
            blocking_key_matched=matched_blocking_key,
            field_level_comparisons=field_sims,
            recommended_action=action,
            num_fields_compared=num_fields_compared,
            hard_conflict=hard_conflict,
            explanation=explanation,
        )

    def find_matches_in_cohort(
        self, target_app: ProviderApplication, candidate_cohort: List[ProviderApplication]
    ) -> List[MatchResult]:
        """Perform multi-pass blocking and comparison of target application against existing cohort."""
        target_keys = self.generate_blocking_keys(target_app)
        matched_pairs: List[MatchResult] = []
        compared_ids: Set[str] = set()

        for cand in candidate_cohort:
            if cand.application_id == target_app.application_id:
                continue
            if cand.application_id in compared_ids:
                continue

            cand_keys = self.generate_blocking_keys(cand)
            
            # Check if any blocking pass matches
            matched_key_name = None
            for key_type, target_val_list in target_keys.items():
                cand_val_list = cand_keys.get(key_type, [])
                for t_val in target_val_list:
                    if t_val in cand_val_list:
                        matched_key_name = f"{key_type}:{t_val}"
                        break
                if matched_key_name:
                    break

            if matched_key_name:
                compared_ids.add(cand.application_id)
                res = self.compare_pair(target_app, cand, matched_key_name)
                if res.is_match or res.is_near_match:
                    matched_pairs.append(res)

        return sorted(matched_pairs, key=lambda x: x.match_score, reverse=True)
