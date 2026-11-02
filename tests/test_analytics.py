"""Space view numbers: posts join to their format, and only posts past 72h count."""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def sent(pid, when, views):
    return dict(id=pid, sentAt=when, metrics=dict(views=views, likes=1, averageTimeWatched=5.0))


class Analytics(unittest.TestCase):
    def test_joins_a_post_to_its_format(self):
        table = analytics.rows([sent("p1", "2026-10-20T10:00:00Z", 100)], {"e1": dict(post="p1")}, [dict(id="e1", template="guess", hook="h")], NOW)
        self.assertEqual((table[0]["template"], table[0]["hook"], table[0]["views"]), ("guess", "h", 100))

    def test_unknown_post_is_kept_as_unlogged(self):
        table = analytics.rows([sent("old", "2026-10-20T10:00:00Z", 50)], {}, [], NOW)
        self.assertEqual(analytics.summary(table)["unlogged"]["n"], 1)

    def test_summary_ignores_posts_younger_than_72h(self):
        table = analytics.rows([sent("a", "2026-10-20T10:00:00Z", 100), sent("b", "2026-11-01T10:00:00Z", 9000)],
                               {"e1": dict(post="a"), "e2": dict(post="b")},
                               [dict(id="e1", template="drift"), dict(id="e2", template="drift")], NOW)
        self.assertEqual(analytics.summary(table)["drift"], dict(n=1, median_views=100, median_avg_watch_s=5.0))

    def test_post_with_no_time_is_skipped(self):
        self.assertEqual(analytics.rows([dict(id="x", metrics={})], {}, [], NOW), [])


if __name__ == "__main__":
    unittest.main()
