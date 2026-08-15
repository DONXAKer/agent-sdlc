import unittest

from scheduler import (
    CycleError, DependencyGraph, ResourcePool, RetryPolicy, Scheduler, Task, TaskState,
)


def ok(value="ok"):
    return lambda: value


class FlakyFn:
    """Падает first_failures раз, потом возвращает value."""

    def __init__(self, first_failures, value="ok"):
        self.left = first_failures
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise RuntimeError("flaky")
        return self.value


class GraphTests(unittest.TestCase):
    def test_cycle_detected(self):
        g = DependencyGraph()
        g.add_dependency("b", "a")
        g.add_dependency("c", "b")
        with self.assertRaises(CycleError):
            g.add_dependency("a", "c")

    def test_self_dependency_rejected(self):
        g = DependencyGraph()
        with self.assertRaises(CycleError):
            g.add_dependency("a", "a")

    def test_transitive_dependents_diamond(self):
        g = DependencyGraph()
        g.add_dependency("b", "a")
        g.add_dependency("c", "a")
        g.add_dependency("d", "b")
        g.add_dependency("d", "c")
        self.assertEqual(g.transitive_dependents("a"), frozenset({"b", "c", "d"}))

    def test_topological_order_layers(self):
        g = DependencyGraph()
        g.add_dependency("b", "a")
        g.add_dependency("c", "a")
        g.add_dependency("d", "b")
        order = g.topological_order()
        self.assertLess(order.index("a"), order.index("b"))
        self.assertLess(order.index("b"), order.index("d"))


class SchedulerTests(unittest.TestCase):
    def test_priority_order_within_ready(self):
        s = Scheduler()
        s.submit(Task("low", ok(), priority=1))
        s.submit(Task("high", ok(), priority=10))
        s.run()
        started = [tid for _, e, tid in s.events if e == "start"]
        self.assertEqual(started, ["high", "low"])

    def test_dependency_gates_execution(self):
        s = Scheduler()
        s.submit(Task("a", ok()))
        s.submit(Task("b", ok(), priority=100), depends_on=["a"])
        s.run()
        started = [tid for _, e, tid in s.events if e == "start"]
        self.assertEqual(started, ["a", "b"])

    def test_failure_blocks_transitive_dependents(self):
        s = Scheduler()
        s.submit(Task("root", FlakyFn(99)))
        s.submit(Task("mid", ok()), depends_on=["root"])
        s.submit(Task("leaf", ok()), depends_on=["mid"])
        states = s.run()
        self.assertEqual(states["root"], TaskState.FAILED)
        self.assertEqual(states["mid"], TaskState.BLOCKED)
        self.assertEqual(states["leaf"], TaskState.BLOCKED)

    def test_retry_recovers_and_backoff_advances_clock(self):
        s = Scheduler()
        fn = FlakyFn(2)
        s.submit(Task("t", fn, retry=RetryPolicy(max_attempts=3, base_delay=1.0, factor=2.0)))
        states = s.run()
        self.assertEqual(states["t"], TaskState.DONE)
        self.assertEqual(fn.calls, 3)
        # backoff: перед 2-й попыткой 1.0, перед 3-й 2.0
        self.assertAlmostEqual(s.clock, 3.0)

    def test_retry_budget_exhausted(self):
        s = Scheduler()
        s.submit(Task("t", FlakyFn(5), retry=RetryPolicy(max_attempts=2)))
        states = s.run()
        self.assertEqual(states["t"], TaskState.FAILED)
        self.assertEqual(s.tasks["t"].attempts, 2)

    def test_resources_released_on_failure(self):
        pool = ResourcePool({"db": 1})
        s = Scheduler(pool)
        s.submit(Task("bad", FlakyFn(99), resources=("db",)))
        s.submit(Task("good", ok(), resources=("db",)))
        states = s.run()
        self.assertEqual(states["good"], TaskState.DONE)
        self.assertEqual(pool.available("db"), 1)

    def test_resource_contention_requeues(self):
        pool = ResourcePool({"gpu": 1})
        s = Scheduler(pool)
        s.submit(Task("a", ok(), priority=5, resources=("gpu",)))
        s.submit(Task("b", ok(), priority=5, resources=("gpu",)))
        states = s.run()
        self.assertEqual(states["a"], TaskState.DONE)
        self.assertEqual(states["b"], TaskState.DONE)

    def test_impossible_resource_fails_fast(self):
        pool = ResourcePool({"gpu": 1})
        s = Scheduler(pool)
        s.submit(Task("t", ok(), resources=("tpu",)))       # ресурса нет в пуле вовсе
        s.submit(Task("dep", ok()), depends_on=["t"])
        states = s.run()                                     # обязан завершиться, не зациклиться
        self.assertEqual(states["t"], TaskState.FAILED)
        self.assertEqual(states["dep"], TaskState.BLOCKED)

    def test_lifo_release_order(self):
        pool = ResourcePool({"conn": 1, "tx": 1})
        order = []
        orig = pool.release_all

        def spy(owner):
            order.append(tuple(pool.held_by(owner)))
            orig(owner)

        pool.release_all = spy
        s = Scheduler(pool)
        s.submit(Task("t", ok(), resources=("conn", "tx")))
        s.run()
        self.assertEqual(order, [("conn", "tx")])
        self.assertEqual(pool.available("conn"), 1)
        self.assertEqual(pool.available("tx"), 1)

    def test_duplicate_id_rejected(self):
        s = Scheduler()
        s.submit(Task("t", ok()))
        with self.assertRaises(KeyError):
            s.submit(Task("t", ok()))

    def test_unknown_dependency_rejected(self):
        s = Scheduler()
        with self.assertRaises(KeyError):
            s.submit(Task("t", ok()), depends_on=["ghost"])

    def test_events_single_done_per_task(self):
        s = Scheduler()
        s.submit(Task("a", ok()))
        s.submit(Task("b", ok()), depends_on=["a"])
        s.run()
        dones = [tid for _, e, tid in s.events if e == "done"]
        self.assertEqual(sorted(dones), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
