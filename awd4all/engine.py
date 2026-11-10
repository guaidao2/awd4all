"""并发执行引擎：与框架无关，只管把插件按目标跑起来并往外抛事件。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from .plugins.base import Finding, Plugin
from .targets import Target

EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class JobSummary:
    total: int = 0
    finished: int = 0
    errors: int = 0
    findings: int = 0
    flags: int = 0
    retries: int = 0
    stopped: bool = False
    elapsed: float = 0.0
    flags_seen: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "finished": self.finished,
            "errors": self.errors,
            "findings": self.findings,
            "flags": self.flags,
            "retries": self.retries,
            "stopped": self.stopped,
            "elapsed": round(self.elapsed, 2),
            "flags_seen": self.flags_seen,
        }


class Engine:
    def __init__(
        self,
        concurrency: int = 32,
        delay: float = 0.0,
        stop_on_hit: bool = False,
        retries: int = 0,
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self.delay = max(0.0, float(delay))
        self.stop_on_hit = stop_on_hit
        self.retries = max(0, int(retries))

    def run(
        self,
        plugin: Plugin,
        targets: list[Target],
        params: dict[str, Any],
        on_event: EventCallback | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> JobSummary:
        emit_lock = threading.Lock()
        stop_event = threading.Event()
        summary = JobSummary(total=len(targets))
        flags_seen: set[str] = set()
        started_at = time.monotonic()

        def emit(event: dict[str, Any]) -> None:
            if on_event is None:
                return
            with emit_lock:
                on_event(event)

        def worker(target: Target) -> None:
            if stop_event.is_set():
                return
            if should_stop and should_stop():
                stop_event.set()
                return

            emit({"type": "target_start", "target": target.label})
            findings: list[Finding] = []
            last_error: Exception | None = None
            attempts = self.retries + 1

            for attempt in range(1, attempts + 1):
                try:
                    findings = plugin.run(
                        target,
                        params,
                        lambda level, message, label=target.label: emit({
                            "type": "log",
                            "level": level,
                            "message": message,
                            "target": label,
                        }),
                    ) or []
                    last_error = None
                    break
                except Exception as exc:  # 单个目标失败不能拖垮整场
                    last_error = exc
                    if attempt < attempts:
                        with emit_lock:
                            summary.retries += 1
                        emit({
                            "type": "log",
                            "level": "warn",
                            "target": target.label,
                            "message": f"第 {attempt} 次失败（{type(exc).__name__}），重试中",
                        })
                    if stop_event.is_set() or (should_stop and should_stop()):
                        break

            with emit_lock:
                summary.finished += 1
                if last_error is not None:
                    summary.errors += 1

            if last_error is not None:
                emit({
                    "type": "target_error",
                    "target": target.label,
                    "message": f"{type(last_error).__name__}: {last_error}",
                })

            for finding in findings:
                with emit_lock:
                    summary.findings += 1
                    if finding.flag and finding.flag not in flags_seen:
                        flags_seen.add(finding.flag)
                        summary.flags += 1
                        summary.flags_seen.append(finding.flag)
                emit({"type": "finding", "finding": finding.to_dict()})
                if self.stop_on_hit and finding.flag:
                    stop_event.set()

            emit({"type": "target_done", "target": target.label})

        executor = ThreadPoolExecutor(max_workers=self.concurrency)
        try:
            futures = []
            for target in targets:
                if stop_event.is_set():
                    break
                futures.append(executor.submit(worker, target))
                if self.delay:
                    time.sleep(self.delay)
            for future in as_completed(futures):
                future.result()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        summary.stopped = stop_event.is_set()
        summary.elapsed = time.monotonic() - started_at
        return summary
