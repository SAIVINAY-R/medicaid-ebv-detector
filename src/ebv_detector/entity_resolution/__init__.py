"""M1 Entity Resolution and Probabilistic Record Linkage Package."""

from .comparators import (
    clean_phone,
    double_metaphone,
    jaro_similarity,
    jaro_winkler_similarity,
    levenshtein_distance,
    levenshtein_similarity,
    normalize_string,
    token_set_similarity,
)
from .fellegi_sunter import FellegiSunterResolver, FieldWeightConfig
from .near_match_queue import AdjudicationAction, NearMatchQueue, QueueItem

__all__ = [
    "clean_phone",
    "double_metaphone",
    "jaro_similarity",
    "jaro_winkler_similarity",
    "levenshtein_distance",
    "levenshtein_similarity",
    "normalize_string",
    "token_set_similarity",
    "FellegiSunterResolver",
    "FieldWeightConfig",
    "AdjudicationAction",
    "NearMatchQueue",
    "QueueItem",
]
