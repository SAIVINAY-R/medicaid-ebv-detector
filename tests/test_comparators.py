"""Unit tests for string and phonetic comparators in M1 Entity Resolution."""

import unittest

from ebv_detector.entity_resolution.comparators import (
    clean_phone,
    double_metaphone,
    jaro_similarity,
    jaro_winkler_similarity,
    levenshtein_distance,
    levenshtein_similarity,
    normalize_string,
    token_set_similarity,
)


class TestComparators(unittest.TestCase):

    def test_normalize_string(self):
        self.assertEqual(normalize_string("  Apex Autism & Behavioral Services, LLC!  "), "apex autism behavioral services llc")
        self.assertEqual(normalize_string(""), "")
        self.assertEqual(normalize_string(None), "")

    def test_clean_phone(self):
        self.assertEqual(clean_phone("(919) 555-8822"), "9195558822")
        self.assertEqual(clean_phone("+1-919-555-8822"), "9195558822")
        self.assertEqual(clean_phone("555-8822"), "") # Invalid length

    def test_jaro_winkler_similarity(self):
        # Exact match
        self.assertAlmostEqual(jaro_winkler_similarity("Triangle Pediatrics", "Triangle Pediatrics"), 1.0)
        # Minor typo with common prefix
        sim = jaro_winkler_similarity("Triangle Pediatrics", "Triangel Pediatrics")
        self.assertGreater(sim, 0.90)
        # Completely different
        self.assertLess(jaro_winkler_similarity("Triangle Pediatrics", "Apex Behavioral Solutions"), 0.60)

    def test_levenshtein_distance_and_similarity(self):
        self.assertEqual(levenshtein_distance("Raleigh", "Raleigh"), 0)
        self.assertEqual(levenshtein_distance("Raleigh", "Ralegh"), 1)
        self.assertGreater(levenshtein_similarity("Raleigh", "Ralegh"), 0.85)

    def test_token_set_similarity(self):
        # Permuted tokens
        sim = token_set_similarity("Behavioral Health Solutions LLC", "Solutions Health Behavioral LLC")
        self.assertAlmostEqual(sim, 1.0)

    def test_double_metaphone(self):
        # Phonetically identical/similar names
        p1, s1 = double_metaphone("Smith")
        p2, s2 = double_metaphone("Smyth")
        self.assertEqual(p1, p2)

        # Silent letters
        p_kn, _ = double_metaphone("Knight")
        p_n, _ = double_metaphone("Night")
        self.assertEqual(p_kn, p_n)


if __name__ == "__main__":
    unittest.main()
