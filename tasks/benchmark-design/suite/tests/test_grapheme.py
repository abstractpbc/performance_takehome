"""Direct tests for the grapheme-boundary split counter (round-3 review nit)."""

import unittest

from bench.gates import grapheme_boundary_splits


def deltas(*texts: str) -> list[tuple[str, str, float]]:
    return [("content", t, float(i)) for i, t in enumerate(texts)]


class GraphemeBoundarySplitTests(unittest.TestCase):
    def test_clean_ascii_deltas_count_zero(self) -> None:
        self.assertEqual(grapheme_boundary_splits(deltas("Hello", " world", ".")), 0)

    def test_split_inside_a_multibyte_code_point_counts(self) -> None:
        # The runner decodes half a UTF-8 sequence with surrogateescape -> lone surrogate.
        self.assertEqual(grapheme_boundary_splits(deltas("caf", "\udce9")), 1)

    def test_split_inside_a_combining_sequence_counts(self) -> None:
        self.assertEqual(grapheme_boundary_splits(deltas("e", "\u0301t\u00e9")), 1)
        self.assertEqual(grapheme_boundary_splits(deltas("\U0001f468", "\u200d\U0001f469")), 1)
        self.assertEqual(grapheme_boundary_splits(deltas("\U0001f468\u200d", "\U0001f469")), 1)

    def test_two_complete_adjacent_flags_do_not_count(self) -> None:
        canada, switzerland = "\U0001f1e8\U0001f1e6", "\U0001f1e8\U0001f1ed"
        self.assertEqual(grapheme_boundary_splits(deltas(canada, switzerland)), 0)
        self.assertEqual(grapheme_boundary_splits(deltas(canada + " ", switzerland)), 0)

    def test_half_a_flag_counts(self) -> None:
        self.assertEqual(grapheme_boundary_splits(deltas("\U0001f1e8", "\U0001f1e6")), 1)

    def test_reasoning_channel_and_empty_deltas_are_ignored(self) -> None:
        mixed = [("reasoning", "\u0301", 0.0), ("content", "", 1.0), ("content", "ok", 2.0)]
        self.assertEqual(grapheme_boundary_splits(mixed), 0)


if __name__ == "__main__":
    unittest.main()
