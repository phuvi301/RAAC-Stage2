"""Checks and scheduling on the task graph. Pure code: no model decides what may run."""

from __future__ import annotations

from collections import deque

from .models import Spec, TaskGraph


def problems(graph: TaskGraph, spec: Spec | None = None, *, max_tasks: int = 40) -> list[str]:
    """Every reason the plan cannot be executed as written; an empty list means it is valid."""
    found: list[str] = []
    ids = [task.id for task in graph.tasks]
    known = set(ids)
    if len(ids) != len(known):
        duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
        found.append(f"duplicate task ids: {', '.join(duplicates)}")
    if len(ids) > max_tasks:
        found.append(f"{len(ids)} tasks exceed the limit of {max_tasks}")
    for task in graph.tasks:
        for dependency in task.depends_on:
            if dependency == task.id:
                found.append(f"{task.id} depends on itself")
            elif dependency not in known:
                found.append(f"{task.id} depends on unknown task {dependency}")
    if not found:
        cycle = find_cycle(graph)
        if cycle:
            found.append("dependency cycle: " + " -> ".join(cycle))
    if spec is not None:
        requirement_ids = {req.id for req in spec.requirements}
        ac_ids = set(spec.ac_ids())
        for task in graph.tasks:
            for req in task.requirement_ids:
                if req not in requirement_ids:
                    found.append(f"{task.id} refers to unknown requirement {req}")
            for ac in task.acceptance_criteria:
                if ac not in ac_ids:
                    found.append(f"{task.id} refers to unknown acceptance criterion {ac}")
        covered = {ac for task in graph.tasks for ac in task.acceptance_criteria}
        missing = sorted(ac_ids - covered)
        if missing:
            found.append("acceptance criteria not covered by any task: " + ", ".join(missing))
    return found


def find_cycle(graph: TaskGraph) -> list[str]:
    """One dependency cycle as a path (first node repeated at the end), or []."""
    edges = {task.id: list(task.depends_on) for task in graph.tasks}
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done
    path: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = 1
        path.append(node)
        for dependency in edges.get(node, []):
            if state.get(dependency) == 1:
                return path[path.index(dependency):] + [dependency]
            if dependency in edges and dependency not in state:
                cycle = visit(dependency)
                if cycle:
                    return cycle
        state[node] = 2
        path.pop()
        return []

    for node in sorted(edges):
        if node not in state:
            cycle = visit(node)
            if cycle:
                return cycle
    return []


def topological_order(graph: TaskGraph) -> list[str]:
    """Dependencies first; among tasks that are ready together, priority then ID decides."""
    indegree = {task.id: len(task.depends_on) for task in graph.tasks}
    dependents: dict[str, list[str]] = {task.id: [] for task in graph.tasks}
    for task in graph.tasks:
        for dependency in task.depends_on:
            dependents[dependency].append(task.id)
    by_id = {task.id: task for task in graph.tasks}
    ready = sorted((tid for tid, n in indegree.items() if n == 0), key=lambda tid: _rank(by_id[tid]))
    order: list[str] = []
    queue = deque(ready)
    while queue:
        current = queue.popleft()
        order.append(current)
        released = []
        for dependent in dependents[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                released.append(dependent)
        queue = deque(sorted([*queue, *released], key=lambda tid: _rank(by_id[tid])))
    if len(order) != len(by_id):
        raise ValueError("the task graph has a cycle")
    return order


def ready_tasks(graph: TaskGraph, status: dict[str, str]) -> list[str]:
    """Pending tasks whose dependencies have all passed, best first."""
    by_id = {task.id: task for task in graph.tasks}
    ready = [task.id for task in graph.tasks
             if status.get(task.id, "pending") == "pending"
             and all(status.get(dep) == "passed" for dep in task.depends_on)]
    return sorted(ready, key=lambda tid: _rank(by_id[tid]))


def dependents_of(graph: TaskGraph, task_id: str) -> list[str]:
    """Every task that needs ``task_id``, directly or indirectly (blocked when it fails)."""
    children: dict[str, list[str]] = {task.id: [] for task in graph.tasks}
    for task in graph.tasks:
        for dependency in task.depends_on:
            children.setdefault(dependency, []).append(task.id)
    seen: list[str] = []
    stack = list(children.get(task_id, []))
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.append(current)
            stack.extend(children.get(current, []))
    return sorted(seen)


def _rank(task):
    number = int(task.id[1:]) if task.id[1:].isdigit() else 0
    return (task.priority, number)
