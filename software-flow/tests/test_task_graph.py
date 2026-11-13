from swflow.models import Requirement, Spec, TaskGraph, TaskNode
from swflow.task_graph import dependents_of, find_cycle, problems, ready_tasks, topological_order


def task(task_id, depends=(), ac=("FR-1.AC1",), priority=3):
    return TaskNode(id=task_id, title=task_id, description="d", depends_on=list(depends),
                    acceptance_criteria=list(ac), priority=priority, size="S")


def spec():
    return Spec(summary="s", requirements=[
        Requirement(id="FR-1", kind="functional", title="t", statement="s", priority="must",
                    acceptance_criteria=["a", "b"])])


def test_valid_graph_orders_dependencies_first_then_priority():
    graph = TaskGraph(tasks=[task("T1"), task("T2", ["T1"], priority=2), task("T3", priority=1),
                             task("T4", ["T2", "T3"], ac=["FR-1.AC2"])])
    assert problems(graph, spec()) == []
    assert topological_order(graph) == ["T3", "T1", "T2", "T4"]


def test_unknown_self_and_cyclic_dependencies_are_reported():
    assert problems(TaskGraph(tasks=[task("T1", ["T9"])])) == ["T1 depends on unknown task T9"]
    assert problems(TaskGraph(tasks=[task("T1", ["T1"])])) == ["T1 depends on itself"]
    cyclic = TaskGraph(tasks=[task("T1", ["T3"]), task("T2", ["T1"]), task("T3", ["T2"])])
    assert find_cycle(cyclic) == ["T1", "T3", "T2", "T1"]
    assert problems(cyclic)[0].startswith("dependency cycle")


def test_every_acceptance_criterion_must_be_covered_and_exist():
    graph = TaskGraph(tasks=[task("T1", ac=["FR-1.AC1", "FR-9.AC1"])])
    found = problems(graph, spec())
    assert "T1 refers to unknown acceptance criterion FR-9.AC1" in found
    assert "acceptance criteria not covered by any task: FR-1.AC2" in found


def test_ready_tasks_wait_for_passed_dependencies_and_failures_block_descendants():
    graph = TaskGraph(tasks=[task("T1"), task("T2", ["T1"]), task("T3", ["T2"]), task("T4")])
    assert ready_tasks(graph, {}) == ["T1", "T4"]
    assert ready_tasks(graph, {"T1": "passed"}) == ["T2", "T4"]
    assert ready_tasks(graph, {"T1": "failed", "T4": "passed"}) == []
    assert dependents_of(graph, "T1") == ["T2", "T3"]


def test_limits_and_duplicates():
    assert problems(TaskGraph(tasks=[task("T1"), task("T1")]))[0] == "duplicate task ids: T1"
    assert "3 tasks exceed the limit of 2" in problems(TaskGraph(tasks=[task("T1"), task("T2"), task("T3")]),
                                                      max_tasks=2)
