"""Планировщик с приоритетами, ретраями с backoff и LIFO-освобождением ресурсов.

Исполнение синхронное, время симулируется внутренними часами: детерминизм важнее
реализма — на нём держатся тесты гонок ретраев.
"""

import heapq
from enum import Enum

from .graph import DependencyGraph


class TaskState(Enum):
    PENDING = "pending"        # ждёт зависимостей
    READY = "ready"            # зависимости закрыты, стоит в очереди
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"  # упала, ждёт backoff-окна
    DONE = "done"
    FAILED = "failed"          # попытки исчерпаны
    BLOCKED = "blocked"        # зависимость провалилась — не запускалась


class RetryPolicy:
    def __init__(self, max_attempts=1, base_delay=1.0, factor=2.0, max_delay=60.0):
        if max_attempts < 1:
            raise ValueError("max_attempts >= 1")
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.factor = factor
        self.max_delay = max_delay

    def delay_for(self, attempt):
        """Задержка перед попыткой attempt (нумерация с 2: перед первой задержки нет)."""
        d = self.base_delay * (self.factor ** (attempt - 2))
        return min(d, self.max_delay)


class ResourcePool:
    """Именованные ресурсы со счётчиком ёмкости и LIFO-порядком освобождения.

    Инвариант: release идёт строго в обратном порядке acquire того же владельца —
    смешанный порядок ломает вложенные ресурсы (соединение поверх транзакции).
    """

    def __init__(self, capacities):
        self._cap = dict(capacities)
        self._initial = dict(capacities)  # исходные ёмкости — для проверки выполнимости
        self._held = {}  # owner -> [name, ...] в порядке захвата

    def can_ever_satisfy(self, names):
        """Выполним ли запрос в принципе: у пустого пула он не выполним никогда."""
        need = {}
        for n in names:
            need[n] = need.get(n, 0) + 1
        return all(self._initial.get(n, 0) >= c for n, c in need.items())

    def acquire(self, owner, names):
        got = []
        for name in names:
            if self._cap.get(name, 0) <= 0:
                # откат уже взятого — в обратном порядке
                for g in reversed(got):
                    self._cap[g] += 1
                return False
            self._cap[name] -= 1
            got.append(name)
        self._held.setdefault(owner, []).extend(got)
        return True

    def release_all(self, owner):
        """Вернуть всё, чем владеет owner, в LIFO-порядке."""
        for name in reversed(self._held.pop(owner, [])):
            self._cap[name] = self._cap.get(name, 0) + 1

    def held_by(self, owner):
        return tuple(self._held.get(owner, ()))

    def available(self, name):
        return self._cap.get(name, 0)


class Task:
    def __init__(self, task_id, fn, priority=0, resources=(), retry=None):
        self.id = task_id
        self.fn = fn
        self.priority = priority
        self.resources = tuple(resources)
        self.retry = retry or RetryPolicy()
        self.state = TaskState.PENDING
        self.attempts = 0
        self.result = None
        self.error = None


class Scheduler:
    """Однопоточный планировщик: submit -> run(); события пишутся в self.events."""

    def __init__(self, resource_pool=None):
        self.graph = DependencyGraph()
        self.tasks = {}
        self.pool = resource_pool or ResourcePool({})
        self.events = []          # (clock, event, task_id)
        self.clock = 0.0
        self._ready = []          # heap: (-priority, seq, task_id)
        self._retry_wait = []     # heap: (wake_at, seq, task_id)
        self._seq = 0

    # -- сборка --------------------------------------------------------

    def submit(self, task, depends_on=()):
        if task.id in self.tasks:
            raise KeyError(f"duplicate task id: {task.id}")
        self.tasks[task.id] = task
        self.graph.add_node(task.id)
        for dep in depends_on:
            if dep not in self.tasks:
                raise KeyError(f"unknown dependency: {dep}")
            self.graph.add_dependency(task.id, dep)
        return task

    # -- исполнение ----------------------------------------------------

    def run(self):
        self._promote_ready()
        while self._ready or self._retry_wait:
            if not self._ready:
                # прыжок часов к ближайшему пробуждению
                wake_at, _, tid = heapq.heappop(self._retry_wait)
                self.clock = max(self.clock, wake_at)
                self._enqueue_ready(tid)
                continue
            _, _, tid = heapq.heappop(self._ready)
            self._execute(tid)
            self._promote_ready(tid)
        return {t.id: t.state for t in self.tasks.values()}

    def _promote_ready(self, completed=None):
        """Без аргумента — стартовый скан всех задач; с id завершившейся — продвижение
        только её зависимых по обратным рёбрам графа: O(соседей) вместо полного скана
        после каждого исполнения. Обход зависимых сортируется — порядок событий детерминирован.
        """
        if completed is None:
            for t in self.tasks.values():
                if t.state is TaskState.PENDING and self._deps_done(t.id):
                    self._enqueue_ready(t.id)
            return
        t = self.tasks[completed]
        if t.state is TaskState.DONE:
            for dep_id in sorted(self.graph.dependents(completed)):
                dep = self.tasks[dep_id]
                if dep.state is TaskState.PENDING and self._deps_done(dep_id):
                    self._enqueue_ready(dep_id)
        elif t.state is TaskState.FAILED:
            for dep_id in sorted(self.graph.transitive_dependents(completed)):
                dep = self.tasks[dep_id]
                if dep.state is TaskState.PENDING:
                    dep.state = TaskState.BLOCKED
                    self._emit("blocked", dep_id)

    def _deps_done(self, tid):
        return all(self.tasks[d].state is TaskState.DONE
                   for d in self.graph.dependencies(tid))

    def _enqueue_ready(self, tid):
        t = self.tasks[tid]
        t.state = TaskState.READY
        self._seq += 1
        heapq.heappush(self._ready, (-t.priority, self._seq, tid))
        self._emit("ready", tid)

    def _execute(self, tid):
        t = self.tasks[tid]
        if t.state is not TaskState.READY:
            return
        if t.resources and not self.pool.can_ever_satisfy(t.resources):
            # запрос невыполним ни при каком освобождении — иначе run() зациклится
            t.error = ValueError(f"resources never satisfiable: {t.resources}")
            t.state = TaskState.FAILED
            self._emit("failed", tid)
            return
        if t.resources:
            # к началу каждого исполнения пул полон: цикл однопоточный, а _execute
            # возвращает всё через release_all на каждом пути — выполнимый запрос
            # (can_ever_satisfy выше) захватывается всегда; провал захвата = сломанный
            # инвариант пула, падаем громко, а не крутим недостижимый requeue
            acquired = self.pool.acquire(tid, t.resources)
            assert acquired, f"resource pool not full at execute: {t.resources}"
        t.state = TaskState.RUNNING
        t.attempts += 1
        self._emit("start", tid)
        try:
            t.result = t.fn()
        except Exception as e:  # noqa: BLE001 — граница исполнения задачи
            t.error = e
            self.pool.release_all(tid)
            if t.attempts < t.retry.max_attempts:
                t.state = TaskState.RETRY_WAIT
                wake = self.clock + t.retry.delay_for(t.attempts + 1)
                self._seq += 1
                heapq.heappush(self._retry_wait, (wake, self._seq, tid))
                self._emit("retry_scheduled", tid)
            else:
                t.state = TaskState.FAILED
                self._emit("failed", tid)
            return
        self.pool.release_all(tid)
        t.state = TaskState.DONE
        self._emit("done", tid)

    def _emit(self, event, tid):
        self.events.append((self.clock, event, tid))

    def events_for(self, tid):
        return [(c, e) for c, e, i in self.events if i == tid]
