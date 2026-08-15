"""Граф зависимостей задач: DAG с проверкой циклов и обходами в обе стороны."""

from collections import deque


class CycleError(ValueError):
    """Добавление ребра замкнуло бы цикл."""

    def __init__(self, path):
        self.path = list(path)
        super().__init__(" -> ".join(self.path))


class DependencyGraph:
    """Ориентированный граф «задача -> её зависимости».

    Ребро (a, b) означает «a зависит от b»: b обязана завершиться раньше a.
    Инвариант: граф ацикличен после каждой публичной операции.
    """

    def __init__(self):
        self._deps = {}       # node -> set(того, от чего зависит)
        self._dependents = {} # node -> set(тех, кто зависит от него)

    def add_node(self, node):
        self._deps.setdefault(node, set())
        self._dependents.setdefault(node, set())

    def add_dependency(self, node, depends_on):
        if node == depends_on:
            raise CycleError([node, node])
        self.add_node(node)
        self.add_node(depends_on)
        # цикл появится, если из depends_on уже достижим node
        path = self._find_path(depends_on, node)
        if path is not None:
            raise CycleError([node] + path)
        self._deps[node].add(depends_on)
        self._dependents[depends_on].add(node)

    def _find_path(self, src, dst):
        """Путь src -> ... -> dst по рёбрам зависимостей, или None."""
        if src == dst:
            return [src]
        stack = [(src, [src])]
        seen = {src}
        while stack:
            cur, path = stack.pop()
            for nxt in self._deps.get(cur, ()):
                if nxt == dst:
                    return path + [nxt]
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, path + [nxt]))
        return None

    def dependencies(self, node):
        return frozenset(self._deps.get(node, ()))

    def dependents(self, node):
        return frozenset(self._dependents.get(node, ()))

    def transitive_dependents(self, node):
        """Все, кто прямо или косвенно зависит от node. Сам node не входит.

        Ромбы схлопываются: каждый узел ровно один раз.
        """
        out = set()
        q = deque(self._dependents.get(node, ()))
        while q:
            cur = q.popleft()
            if cur in out:
                continue
            out.add(cur)
            q.extend(self._dependents.get(cur, ()))
        return frozenset(out)

    def topological_order(self):
        """Слои Кана: внутри слоя порядок детерминирован сортировкой."""
        indeg = {n: len(d) for n, d in self._deps.items()}
        layer = sorted(n for n, d in indeg.items() if d == 0)
        order = []
        while layer:
            order.extend(layer)
            nxt = set()
            for n in layer:
                for dep in self._dependents.get(n, ()):
                    indeg[dep] -= 1
                    if indeg[dep] == 0:
                        nxt.add(dep)
            layer = sorted(nxt)
        if len(order) != len(self._deps):
            raise CycleError(["<unresolved>"])
        return order

    def nodes(self):
        return frozenset(self._deps)
