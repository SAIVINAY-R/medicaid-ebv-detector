"""String and phonetic comparators for M1 Entity Resolution.

Includes:
- Jaro-Winkler string similarity with prefix adjustment
- Levenshtein edit distance and normalized similarity
- Double Metaphone phonetic encoding
- Alphanumeric token set Jaccard similarity
- Phone number and address component normalizers
"""

import math
import re
from typing import List, Optional, Set, Tuple


def normalize_string(s: Optional[str]) -> str:
    """Normalize string by removing non-alphanumeric chars, lowercasing, and collapsing whitespace."""
    if not s:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", s).lower()
    return " ".join(cleaned.split())


def clean_phone(phone: Optional[str]) -> str:
    """Extract 10 digits from phone string."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def jaro_similarity(s1: str, s2: str) -> float:
    """Standard Jaro string similarity metric between 0.0 and 1.0."""
    s1, s2 = normalize_string(s1), normalize_string(s2)
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0

    len1, len2 = len(s1), len(s2)
    max_dist = max(len1, len2) // 2 - 1
    if max_dist < 0:
        max_dist = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - max_dist)
        end = min(i + max_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j]:
                continue
            if s1[i] == s2[j]:
                s1_matches[i] = True
                s2_matches[j] = True
                matches += 1
                break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    transpositions //= 2
    return (matches / len1 + matches / len2 + (matches - transpositions) / matches) / 3.0


def jaro_winkler_similarity(s1: str, s2: str, prefix_weight: float = 0.1, max_prefix: int = 4) -> float:
    """Jaro-Winkler similarity with prefix scaling."""
    s1_norm, s2_norm = normalize_string(s1), normalize_string(s2)
    j_sim = jaro_similarity(s1_norm, s2_norm)
    if j_sim < 0.7:
        return j_sim

    prefix = 0
    for c1, c2 in zip(s1_norm, s2_norm):
        if c1 == c2:
            prefix += 1
            if prefix == max_prefix:
                break
        else:
            break

    return j_sim + prefix * prefix_weight * (1.0 - j_sim)


def levenshtein_distance(s1: str, s2: str) -> int:
    """Classic dynamic programming Levenshtein edit distance."""
    s1, s2 = normalize_string(s1), normalize_string(s2)
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1] + [0] * len(s2)
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (0 if c1 == c2 else 1)
            curr_row[j + 1] = min(insertions, deletions, substitutions)
        prev_row = curr_row

    return prev_row[-1]


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Normalized similarity based on edit distance: 1 - (dist / max(len1, len2))."""
    s1, s2 = normalize_string(s1), normalize_string(s2)
    max_l = max(len(s1), len(s2))
    if max_l == 0:
        return 1.0
    dist = levenshtein_distance(s1, s2)
    return max(0.0, 1.0 - (dist / max_l))


def token_set_similarity(s1: str, s2: str) -> float:
    """Jaccard similarity of normalized words."""
    tokens1 = set(normalize_string(s1).split())
    tokens2 = set(normalize_string(s2).split())
    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0
    intersection = len(tokens1 & tokens2)
    union = len(tokens1 | tokens2)
    return intersection / union if union > 0 else 0.0


def double_metaphone(name: str) -> Tuple[str, str]:
    """Phonetic encoding algorithm for matching names across phonetic spellings.
    
    Produces (primary_code, secondary_code).
    Handles standard English / European phonetics, silent letters, and multi-letter combinations.
    """
    s = normalize_string(name).upper()
    if not s:
        return ("", "")
    
    # Common prefixes with silent letters
    if s.startswith(("GN", "KN", "PN", "WR", "PS")):
        s = s[1:]
    elif s.startswith("X"):
        s = "S" + s[1:]

    primary: List[str] = []
    secondary: List[str] = []
    length = len(s)
    idx = 0

    while idx < length and len(primary) < 4:
        ch = s[idx]
        
        # Vowels at start get 'A'
        if ch in "AEIOUY":
            if idx == 0:
                primary.append("A")
                secondary.append("A")
            idx += 1
            continue

        if ch == "B":
            primary.append("P")
            secondary.append("P")
            if idx + 1 < length and s[idx + 1] == "B":
                idx += 1
        elif ch == "C":
            # Various C sounds: CH, CI, CE, CK
            if idx + 1 < length and s[idx + 1] in "EIY":
                primary.append("S")
                secondary.append("S")
            elif idx + 1 < length and s[idx + 1] == "H":
                primary.append("X")
                secondary.append("X")
                idx += 1
            else:
                primary.append("K")
                secondary.append("K")
        elif ch == "D":
            if idx + 1 < length and s[idx + 1] == "G":
                primary.append("J")
                secondary.append("J")
                idx += 1
            else:
                primary.append("T")
                secondary.append("T")
        elif ch in "FV":
            primary.append("F")
            secondary.append("F")
        elif ch in "GJ":
            primary.append("K" if ch == "G" else "J")
            secondary.append("J")
        elif ch in "KQ":
            primary.append("K")
            secondary.append("K")
        elif ch == "L":
            primary.append("L")
            secondary.append("L")
        elif ch == "M":
            primary.append("M")
            secondary.append("M")
        elif ch == "N":
            primary.append("N")
            secondary.append("N")
        elif ch == "P":
            if idx + 1 < length and s[idx + 1] == "H":
                primary.append("F")
                secondary.append("F")
                idx += 1
            else:
                primary.append("P")
                secondary.append("P")
        elif ch in "SZ":
            primary.append("S")
            secondary.append("S")
        elif ch == "T":
            if idx + 1 < length and s[idx + 1] == "H":
                primary.append("0")  # 'th' sound
                secondary.append("T")
                idx += 1
            else:
                primary.append("T")
                secondary.append("T")
        elif ch == "W" or ch == "Y":
            # Only encode if followed by vowel
            if idx + 1 < length and s[idx + 1] in "AEIOUY":
                primary.append("A")
                secondary.append("A")
        elif ch == "X":
            primary.append("KS")
            secondary.append("KS")

        idx += 1

    return ("".join(primary)[:4], "".join(secondary)[:4])
