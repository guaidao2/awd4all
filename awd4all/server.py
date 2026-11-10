"""本地 Web 服务：任务管理、事件推送、flag 落库与导出。"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, Response

from .engine import Engine
from .plugins import load_plugins
from .targets import TargetSpecError, parse_targets
from .version import version as __version__

PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR / "web"


class FlagStore:
    """flag 落盘，SQLite 放在当前目录的 data/ 下，随工作目录走。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin TEXT NOT NULL,
                target TEXT NOT NULL,
                flag TEXT NOT NULL,
                evidence TEXT DEFAULT '',
                extra TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                UNIQUE (plugin, target, flag)
            )
            """
        )
        self._conn.commit()

    def add(self, finding: dict[str, Any]) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO flags (plugin, target, flag, evidence, extra, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    finding.get("plugin", ""),
                    finding.get("target", ""),
                    finding.get("flag", ""),
                    (finding.get("evidence") or "")[:4000],
                    json.dumps(finding.get("extra") or {}, ensure_ascii=False),
                    time.time(),
                ),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def list(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, plugin, target, flag, evidence, extra, created_at"
                " FROM flags ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "plugin": row[1],
                "target": row[2],
                "flag": row[3],
                "evidence": row[4],
                "extra": json.loads(row[5] or "{}"),
                "created_at": row[6],
            }
            for row in rows
        ]

    def clear(self) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM flags")
            self._conn.commit()
            return cursor.rowcount


class Job:
    def __init__(self, plugin: str, targets: list, params: dict, options: dict) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.plugin = plugin
        self.targets = targets
        self.params = params
        self.options = options
        self.status = "queued"
        self.summary: dict[str, Any] | None = None
        self.error = ""
        self.created_at = time.time()
        self.cancel_event = threading.Event()
        self.loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._history: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue] = set()

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._history.append(event)
            if len(self._history) > 8000:
                del self._history[:2000]
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                self.loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plugin": self.plugin,
            "targets": len(self.targets),
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
            "created_at": self.created_at,
            "cancelled": self.cancel_event.is_set(),
            "finished": any(event.get("type") == "job_end" for event in self.history()),
        }


class JobRequest(BaseModel):
    plugin: str
    targets: str
    params: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = 32
    delay: float = 0.0
    retries: int = 0
    stop_on_hit: bool = False


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
STORE: FlagStore | None = None


def get_store() -> FlagStore:
    global STORE
    if STORE is None:
        STORE = FlagStore(Path.cwd() / "data" / "awd4all.db")
    return STORE


app = FastAPI(title="awd4all", version=__version__)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__}


@app.get("/api/plugins")
def api_plugins() -> dict[str, Any]:
    plugins = load_plugins()
    return {"plugins": [cls.meta() for cls in plugins.values()]}


@app.post("/api/jobs")
async def create_job(request: JobRequest) -> dict[str, Any]:
    plugins = load_plugins()
    plugin_cls = plugins.get(request.plugin)
    if plugin_cls is None:
        raise HTTPException(status_code=404, detail=f"插件不存在: {request.plugin}")
    try:
        targets = parse_targets(request.targets, default_port=plugin_cls.default_port)
    except TargetSpecError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not targets:
        raise HTTPException(status_code=400, detail="没有解析出任何目标")

    options = {
        "concurrency": max(1, request.concurrency),
        "delay": max(0.0, request.delay),
        "retries": max(0, request.retries),
        "stop_on_hit": request.stop_on_hit,
    }
    job = Job(request.plugin, targets, request.params, options)
    with JOBS_LOCK:
        JOBS[job.id] = job

    thread = threading.Thread(target=_run_job, args=(job, plugin_cls), daemon=True)
    thread.start()
    return {"id": job.id, "targets": len(targets)}


def _run_job(job: Job, plugin_cls: type) -> None:
    plugin = plugin_cls()
    store = get_store()

    def on_event(event: dict[str, Any]) -> None:
        job.publish(event)
        if event.get("type") == "finding":
            finding = event.get("finding") or {}
            if finding.get("flag"):
                store.add(finding)

    job.status = "running"
    job.publish({"type": "job_start", "plugin": job.plugin, "targets": len(job.targets)})
    engine = Engine(
        concurrency=job.options["concurrency"],
        delay=job.options["delay"],
        retries=job.options["retries"],
        stop_on_hit=job.options["stop_on_hit"],
    )
    try:
        summary = engine.run(
            plugin,
            job.targets,
            job.params,
            on_event=on_event,
            should_stop=job.cancel_event.is_set,
        )
        job.summary = summary.to_dict()
        job.status = "stopped" if summary.stopped else "done"
    except Exception as exc:  # 整个任务级别的异常也要让前端知道
        job.error = f"{type(exc).__name__}: {exc}"
        job.status = "error"
        job.publish({"type": "log", "level": "error", "message": job.error})
    finally:
        job.publish({
            "type": "job_end",
            "status": job.status,
            "summary": job.summary or {},
            "error": job.error,
        })


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda item: item.created_at, reverse=True)
    return {"jobs": [job.snapshot() for job in jobs]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job.snapshot()


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    job.cancel_event.set()
    job.publish({"type": "log", "level": "warn", "message": "收到停止请求，等当前目标收尾"})
    return {"id": job.id, "status": "stopping"}


@app.websocket("/api/jobs/{job_id}/ws")
async def job_ws(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    job = JOBS.get(job_id)
    if job is None:
        await websocket.send_json({"type": "error", "message": "任务不存在"})
        await websocket.close()
        return

    queue = job.subscribe()
    ended = False
    try:
        for event in job.history():
            await websocket.send_json(event)
            if event.get("type") == "job_end":
                ended = True
        while not ended:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
            if event.get("type") == "job_end":
                ended = True
    except WebSocketDisconnect:
        pass
    finally:
        job.unsubscribe(queue)


@app.get("/api/flags")
def api_flags(limit: int = 1000) -> dict[str, Any]:
    return {"flags": get_store().list(limit)}


@app.get("/api/flags/export.csv")
def export_flags() -> Response:
    rows = get_store().list(100000)
    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM，Excel 打开中文不乱码
    writer = csv.writer(buffer)
    writer.writerow(["time", "target", "plugin", "flag", "extra"])
    for row in reversed(rows):
        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["created_at"])),
            row["target"],
            row["plugin"],
            row["flag"],
            json.dumps(row["extra"], ensure_ascii=False),
        ])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="awd4all-flags.csv"'},
    )


@app.delete("/api/flags")
def clear_flags() -> dict[str, Any]:
    return {"deleted": get_store().clear()}


@app.exception_handler(TargetSpecError)
async def target_error_handler(_, exc: TargetSpecError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
