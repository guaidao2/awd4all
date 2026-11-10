import unittest

from awd4all.engine import Engine
from awd4all.plugins.base import Finding, Plugin
from awd4all.targets import Target


class FlakyPlugin(Plugin):
    name = "flaky"
    title = "flaky"

    def __init__(self, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def run(self, target, params, emit):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("boom")
        return [Finding(target=target.label, plugin=self.name, flag="flag{engine}")]


class AlwaysFailPlugin(Plugin):
    name = "always_fail"
    title = "always_fail"

    def run(self, target, params, emit):
        raise ConnectionError("cannot connect")


class EngineRetryTests(unittest.TestCase):
    def test_retry_then_success(self):
        plugin = FlakyPlugin(fail_times=1)
        summary = Engine(concurrency=1, retries=2).run(plugin, [Target("1.1.1.1")], {})
        self.assertEqual(summary.errors, 0)
        self.assertEqual(summary.retries, 1)
        self.assertEqual(summary.finished, 1)
        self.assertEqual(summary.flags, 1)
        self.assertEqual(plugin.calls, 2)

    def test_no_retry_reports_error(self):
        summary = Engine(concurrency=1, retries=0).run(AlwaysFailPlugin(), [Target("1.1.1.1")], {})
        self.assertEqual(summary.errors, 1)
        self.assertEqual(summary.retries, 0)
        self.assertEqual(summary.finished, 1)

    def test_retries_exhausted(self):
        summary = Engine(concurrency=1, retries=2).run(AlwaysFailPlugin(), [Target("1.1.1.1")], {})
        self.assertEqual(summary.errors, 1)
        self.assertEqual(summary.retries, 2)


class EngineFlowTests(unittest.TestCase):
    def test_stop_on_hit(self):
        plugin = FlakyPlugin(fail_times=0)
        summary = Engine(concurrency=1, stop_on_hit=True).run(
            plugin, [Target("1.1.1.1"), Target("2.2.2.2")], {}
        )
        self.assertTrue(summary.stopped)
        self.assertEqual(summary.flags, 1)

    def test_should_stop_before_start(self):
        summary = Engine(concurrency=2).run(
            FlakyPlugin(fail_times=0), [Target("1.1.1.1")], {}, should_stop=lambda: True
        )
        self.assertTrue(summary.stopped)
        self.assertEqual(summary.finished, 0)

    def test_events_emitted(self):
        events = []
        Engine(concurrency=2).run(
            FlakyPlugin(fail_times=0), [Target("1.1.1.1")], {}, on_event=events.append
        )
        types = [event["type"] for event in events]
        self.assertIn("target_start", types)
        self.assertIn("finding", types)
        self.assertIn("target_done", types)


if __name__ == "__main__":
    unittest.main()
