from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from ruhu.registry import AgentVersionSnapshot

from .models import EvaluationRuntimeStatus, EvaluationRun, SimulationFixture
from .qualification import qualification_policy
from .service import EvaluationService


@dataclass(slots=True)
class EvaluationRuntime:
    service: EvaluationService
    max_workers: int = 2
    _lock: Lock = field(init=False, repr=False)
    _executor: ThreadPoolExecutor = field(init=False, repr=False)
    _futures: dict[str, Future[Any]] = field(init=False, repr=False, default_factory=dict)
    _last_error: str | None = field(init=False, repr=False, default=None)
    _completed_runs: int = field(init=False, repr=False, default=0)
    _failed_runs: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, self.max_workers),
            thread_name_prefix="ruhu-eval",
        )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def schedule_run(
        self,
        snapshot: AgentVersionSnapshot,
        fixtures: list[SimulationFixture],
        *,
        mode: str = "manual_batch",
        source: str = "worker",
        organization_id: str | None = None,
        gate_eligible: bool = False,
        triggered_by_user_id: str | None = None,
        minimum_pass_rate_ratio: float = 1.0,
        allow_warning_failures: bool = True,
    ) -> EvaluationRun:
        policy = qualification_policy(
            minimum_pass_rate_ratio=minimum_pass_rate_ratio,
            allow_warning_failures=allow_warning_failures,
        )
        run = self.service.create_run(
            snapshot,
            fixtures,
            mode=mode,
            source=source,
            organization_id=organization_id,
            gate_eligible=gate_eligible,
            triggered_by_user_id=triggered_by_user_id,
        )
        with self._lock:
            future = self._executor.submit(
                self._run_job,
                run.evaluation_run_id,
                snapshot,
                fixtures,
                organization_id,
                policy,
            )
            self._futures[run.evaluation_run_id] = future
        return run

    def status(self) -> EvaluationRuntimeStatus:
        with self._lock:
            futures = dict(self._futures)
            queued_runs = sum(1 for future in futures.values() if not future.running() and not future.done())
            running_runs = sum(1 for future in futures.values() if future.running())
            return EvaluationRuntimeStatus(
                max_workers=self.max_workers,
                queued_runs=queued_runs,
                running_runs=running_runs,
                completed_runs=self._completed_runs,
                failed_runs=self._failed_runs,
                last_error=self._last_error,
                active_run_ids=sorted(futures.keys()),
            )

    def wait_for_run(self, evaluation_run_id: str, *, timeout_seconds: float | None = None) -> EvaluationRun:
        future = None
        with self._lock:
            future = self._futures.get(evaluation_run_id)
        if future is not None:
            future.result(timeout=timeout_seconds)
        run = self.service.load_run(evaluation_run_id)
        if run is None:
            raise KeyError(evaluation_run_id)
        return run

    def _run_job(
        self,
        evaluation_run_id: str,
        snapshot: AgentVersionSnapshot,
        fixtures: list[SimulationFixture],
        organization_id: str | None,
        policy,
    ) -> None:
        try:
            self.service.execute_run(
                snapshot,
                fixtures,
                evaluation_run_id=evaluation_run_id,
                organization_id=organization_id,
                policy=policy,
            )
            with self._lock:
                self._completed_runs += 1
        except Exception as exc:
            self.service.fail(
                evaluation_run_id,
                organization_id=organization_id,
                reason=str(exc),
            )
            with self._lock:
                self._failed_runs += 1
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._futures.pop(evaluation_run_id, None)
