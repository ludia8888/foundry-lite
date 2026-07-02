from __future__ import annotations

from foundry_lite.application.services.transform_service import TransformService


class _Recorder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _record(self, method: str, *args: object, **kwargs: object) -> str:
        self.calls.append((method, args, kwargs))
        return f"{self.name}.{method}"


class _DefinitionRecorder(_Recorder):
    def register_transform(self, *args: object, **kwargs: object) -> str:
        return self._record("register_transform", *args, **kwargs)

    def register_sql_transform(self, *args: object, **kwargs: object) -> str:
        return self._record("register_sql_transform", *args, **kwargs)


class _RunRecorder(_Recorder):
    def run_transform(self, *args: object, **kwargs: object) -> str:
        return self._record("run_transform", *args, **kwargs)

    def retry_transform_run(self, *args: object, **kwargs: object) -> str:
        return self._record("retry_transform_run", *args, **kwargs)


class _GraphRecorder(_Recorder):
    def run_transform_graph(self, *args: object, **kwargs: object) -> str:
        return self._record("run_transform_graph", *args, **kwargs)


class _SchedulerRecorder(_Recorder):
    def preview_due_transform_runs(self, *args: object, **kwargs: object) -> str:
        return self._record("preview_due_transform_runs", *args, **kwargs)

    def run_due_transform_runs(self, *args: object, **kwargs: object) -> str:
        return self._record("run_due_transform_runs", *args, **kwargs)


class _DlqReplayRecorder(_Recorder):
    def retry_transform_dead_letter_record(self, *args: object, **kwargs: object) -> str:
        return self._record("retry_transform_dead_letter_record", *args, **kwargs)


def test_transform_service_is_thin_forwarding_entrypoint() -> None:
    service = TransformService()
    definition = _DefinitionRecorder("definition")
    run = _RunRecorder("run")
    graph = _GraphRecorder("graph")
    scheduler = _SchedulerRecorder("scheduler")
    dlq = _DlqReplayRecorder("dlq")
    service.bind_collaborators(
        {
            "transform_definition_service": definition,
            "transform_dlq_replay_service": dlq,
            "transform_graph_service": graph,
            "transform_run_service": run,
            "transform_scheduler_service": scheduler,
        }
    )

    assert service.register_transform("clean", entrypoint="clean.sql", inputs={}, output_dataset_ref="out") == (
        "definition.register_transform"
    )
    assert service.register_sql_transform("clean", sql="select 1", inputs={}, output_dataset_ref="out") == (
        "definition.register_sql_transform"
    )
    assert service.run_transform("clean") == "run.run_transform"
    assert service.retry_transform_run("run_1") == "run.retry_transform_run"
    assert service.run_transform_graph("clean") == "graph.run_transform_graph"
    assert service.preview_due_transform_runs() == "scheduler.preview_due_transform_runs"
    assert service.run_due_transform_runs() == "scheduler.run_due_transform_runs"
    assert service.retry_transform_dead_letter_record("dlq_1", idempotency_key="key") == (
        "dlq.retry_transform_dead_letter_record"
    )

    assert {base.__name__ for base in TransformService.__mro__} == {"TransformService", "CoreService", "object"}
