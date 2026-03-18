from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from typing import Any


STATE_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class TaskRecord:
    task_id: str
    title: str
    details: str
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskRecord":
        timestamp = str(payload.get("updated_at") or payload.get("created_at") or utc_now())
        return cls(
            task_id=str(payload.get("task_id") or f"task-{uuid.uuid4().hex[:12]}"),
            title=str(payload.get("title", "Untitled task")),
            details=str(payload.get("details", "")),
            status=str(payload.get("status", "pending")),
            created_at=str(payload.get("created_at", timestamp)),
            updated_at=timestamp,
        )


@dataclass
class QueueRecord:
    queue_id: str
    name: str
    description: str
    created_at: str
    updated_at: str
    tasks: list[TaskRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tasks"] = [task.to_dict() for task in self.tasks]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QueueRecord":
        timestamp = str(payload.get("updated_at") or payload.get("created_at") or utc_now())
        return cls(
            queue_id=str(payload.get("queue_id") or f"queue-{uuid.uuid4().hex[:12]}"),
            name=str(payload.get("name", "Current session queue")),
            description=str(payload.get("description", "")),
            created_at=str(payload.get("created_at", timestamp)),
            updated_at=timestamp,
            tasks=[TaskRecord.from_dict(item) for item in payload.get("tasks", [])],
        )


@dataclass
class AgentRecord:
    agent_id: str
    name: str
    description: str
    command_args: list[str]
    workdir: str
    queue_id: str
    feature_branch: str
    auto_publish: bool
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    pid: int | None = None
    returncode: int | None = None
    log_path: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentRecord":
        return cls(
            agent_id=str(payload["agent_id"]),
            name=str(payload.get("name", "Untitled agent")),
            description=str(payload.get("description", "")),
            command_args=[str(value) for value in payload.get("command_args", [])],
            workdir=str(payload.get("workdir", "")),
            queue_id=str(payload.get("queue_id", "")),
            feature_branch=str(payload.get("feature_branch", "")),
            auto_publish=bool(payload.get("auto_publish", True)),
            status=str(payload.get("status", "queued")),
            created_at=str(payload.get("created_at", utc_now())),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            pid=payload.get("pid"),
            returncode=payload.get("returncode"),
            log_path=payload.get("log_path"),
            last_error=payload.get("last_error"),
        )


class StateStore:
    def __init__(self, state_file: Path | str) -> None:
        self.state_file = Path(state_file)
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.state_file.exists():
                return {"version": STATE_VERSION, "agents": [], "queues": []}
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            payload.setdefault("version", STATE_VERSION)
            payload.setdefault("agents", [])
            payload.setdefault("queues", [])
            return payload

    def save(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
            payload = {
                "version": STATE_VERSION,
                "agents": payload.get("agents", []),
                "queues": payload.get("queues", []),
            }
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp_path, self.state_file)


class TaskQueueManager:
    def __init__(
        self,
        *,
        project_root: Path | str,
        state_file: Path | str | None = None,
        store: StateStore | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.store = store or StateStore(state_file or (self.project_root / ".mindex" / "task_queues.json"))
        self._lock = threading.RLock()

    def _load_state(self) -> dict[str, Any]:
        payload = self.store.load()
        payload.setdefault("agents", [])
        payload.setdefault("queues", [])
        return payload

    def _read_queues(self) -> list[QueueRecord]:
        payload = self._load_state()
        return [QueueRecord.from_dict(item) for item in payload.get("queues", [])]

    def _write_queues(self, queues: list[QueueRecord]) -> None:
        payload = self._load_state()
        payload["queues"] = [queue.to_dict() for queue in queues]
        self.store.save(payload)

    def list_queues(self) -> list[QueueRecord]:
        with self._lock:
            return self._read_queues()

    def ensure_default_queue(self) -> QueueRecord:
        with self._lock:
            queues = self._read_queues()
            if queues:
                return queues[0]
            timestamp = utc_now()
            queue = QueueRecord(
                queue_id=f"queue-{uuid.uuid4().hex[:12]}",
                name="Current session queue",
                description="Drag tasks to reprioritize upcoming work for this Mindex session.",
                created_at=timestamp,
                updated_at=timestamp,
            )
            queues.append(queue)
            self._write_queues(queues)
            return queue

    def create_queue(self, *, name: str, description: str = "") -> QueueRecord:
        timestamp = utc_now()
        queue = QueueRecord(
            queue_id=f"queue-{uuid.uuid4().hex[:12]}",
            name=name.strip() or "Untitled queue",
            description=description.strip(),
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._lock:
            queues = self._read_queues()
            queues.append(queue)
            self._write_queues(queues)
        return queue

    def update_queue(self, queue_id: str, *, name: str | None = None, description: str | None = None) -> QueueRecord:
        with self._lock:
            queues = self._read_queues()
            for queue in queues:
                if queue.queue_id != queue_id:
                    continue
                if name is not None:
                    queue.name = name.strip() or queue.name
                if description is not None:
                    queue.description = description.strip()
                queue.updated_at = utc_now()
                self._write_queues(queues)
                return queue
        raise KeyError(queue_id)

    def delete_queue(self, queue_id: str) -> None:
        with self._lock:
            queues = self._read_queues()
            retained = [queue for queue in queues if queue.queue_id != queue_id]
            if len(retained) == len(queues):
                raise KeyError(queue_id)
            self._write_queues(retained)
            if not retained:
                self.ensure_default_queue()

    def add_task(
        self,
        queue_id: str,
        *,
        title: str,
        details: str = "",
        status: str = "pending",
    ) -> TaskRecord:
        timestamp = utc_now()
        task = TaskRecord(
            task_id=f"task-{uuid.uuid4().hex[:12]}",
            title=title.strip() or "Untitled task",
            details=details.strip(),
            status=_normalize_task_status(status),
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._lock:
            queues = self._read_queues()
            queue = _find_queue(queues, queue_id)
            queue.tasks.append(task)
            queue.updated_at = utc_now()
            self._write_queues(queues)
        return task

    def update_task(
        self,
        queue_id: str,
        task_id: str,
        *,
        title: str | None = None,
        details: str | None = None,
        status: str | None = None,
    ) -> TaskRecord:
        with self._lock:
            queues = self._read_queues()
            queue = _find_queue(queues, queue_id)
            task = _find_task(queue, task_id)
            if title is not None:
                task.title = title.strip() or task.title
            if details is not None:
                task.details = details.strip()
            if status is not None:
                task.status = _normalize_task_status(status)
            task.updated_at = utc_now()
            queue.updated_at = task.updated_at
            self._write_queues(queues)
            return task

    def delete_task(self, queue_id: str, task_id: str) -> None:
        with self._lock:
            queues = self._read_queues()
            queue = _find_queue(queues, queue_id)
            retained = [task for task in queue.tasks if task.task_id != task_id]
            if len(retained) == len(queue.tasks):
                raise KeyError(task_id)
            queue.tasks = retained
            queue.updated_at = utc_now()
            self._write_queues(queues)

    def reorder_tasks(self, queue_id: str, ordered_task_ids: list[str]) -> QueueRecord:
        with self._lock:
            queues = self._read_queues()
            queue = _find_queue(queues, queue_id)
            tasks_by_id = {task.task_id: task for task in queue.tasks}
            if set(tasks_by_id) != set(ordered_task_ids):
                raise ValueError("ordered_task_ids must include every task in the queue exactly once")
            queue.tasks = [tasks_by_id[task_id] for task_id in ordered_task_ids]
            queue.updated_at = utc_now()
            self._write_queues(queues)
            return queue


class AgentManager:
    def __init__(
        self,
        *,
        project_root: Path | str,
        queue_log_dir: Path | str,
        state_file: Path | str | None = None,
        store: StateStore | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.store = store or StateStore(state_file or (self.project_root / ".mindex" / "task_queues.json"))
        self.queue_log_dir = Path(queue_log_dir).resolve()
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._log_handles: dict[str, Any] = {}
        self._recover_agents()

    def _load_state(self) -> dict[str, Any]:
        payload = self.store.load()
        payload.setdefault("agents", [])
        payload.setdefault("queues", [])
        return payload

    def _read_agents(self) -> list[AgentRecord]:
        payload = self._load_state()
        agents = [AgentRecord.from_dict(item) for item in payload.get("agents", [])]
        for agent in agents:
            self._refresh_agent(agent)
        self._write_agents(agents)
        return agents

    def _write_agents(self, agents: list[AgentRecord]) -> None:
        payload = self._load_state()
        payload["agents"] = [agent.to_dict() for agent in agents]
        self.store.save(payload)

    def _recover_agents(self) -> None:
        changed = False
        agents = [AgentRecord.from_dict(item) for item in self._load_state().get("agents", [])]
        for agent in agents:
            if agent.status == "running":
                agent.status = "disconnected"
                agent.last_error = "Server restarted while this agent was running."
                changed = True
        if changed:
            self._write_agents(agents)

    def list_agents(self) -> list[AgentRecord]:
        with self._lock:
            return self._read_agents()

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        with self._lock:
            for agent in self._read_agents():
                if agent.agent_id == agent_id:
                    return agent
        return None

    def create_agent(
        self,
        *,
        name: str,
        description: str,
        command_args: list[str],
        workdir: Path | str,
        queue_id: str = "",
        feature_branch: str = "",
        auto_publish: bool = True,
    ) -> AgentRecord:
        if not command_args:
            raise ValueError("command_args must not be empty")
        resolved_workdir = Path(workdir).resolve()
        self._validate_workdir(resolved_workdir)
        agent = AgentRecord(
            agent_id=f"agent-{uuid.uuid4().hex[:12]}",
            name=name.strip() or "Untitled agent",
            description=description.strip(),
            command_args=command_args,
            workdir=str(resolved_workdir),
            queue_id=queue_id.strip(),
            feature_branch=feature_branch.strip(),
            auto_publish=auto_publish,
            status="queued",
            created_at=utc_now(),
        )
        with self._lock:
            agents = self._read_agents()
            agents.append(agent)
            self._write_agents(agents)
        return agent

    def delete_agent(self, agent_id: str) -> None:
        with self._lock:
            agents = self._read_agents()
            retained: list[AgentRecord] = []
            found = False
            for agent in agents:
                if agent.agent_id != agent_id:
                    retained.append(agent)
                    continue
                found = True
                if agent.status == "running":
                    raise ValueError("stop the agent before deleting it")
            if not found:
                raise KeyError(agent_id)
            self._write_agents(retained)
            self._processes.pop(agent_id, None)
            handle = self._log_handles.pop(agent_id, None)
            if handle is not None:
                handle.close()

    def start_agent(self, agent_id: str) -> AgentRecord:
        with self._lock:
            agents = self._read_agents()
            for agent in agents:
                if agent.agent_id != agent_id:
                    continue
                if agent.status == "running":
                    return agent
                self.queue_log_dir.mkdir(parents=True, exist_ok=True)
                log_path = self.queue_log_dir / f"{agent.agent_id}.log"
                env = os.environ.copy()
                env["MINDEX_AUTO_PUBLISH"] = "1" if agent.auto_publish else "0"
                source_root = Path(__file__).resolve().parents[1]
                existing_pythonpath = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = (
                    f"{source_root}{os.pathsep}{existing_pythonpath}"
                    if existing_pythonpath
                    else str(source_root)
                )
                if agent.feature_branch:
                    env["MINDEX_FEATURE_BRANCH"] = agent.feature_branch
                stdout_handle = log_path.open("a", encoding="utf-8")
                stdout_handle.write(f"[{utc_now()}] starting {' '.join(agent.command_args)}\n")
                stdout_handle.flush()
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "mindex", *agent.command_args],
                        cwd=agent.workdir,
                        env=env,
                        stdout=stdout_handle,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                except Exception:
                    stdout_handle.close()
                    raise
                self._processes[agent.agent_id] = process
                self._log_handles[agent.agent_id] = stdout_handle
                agent.status = "running"
                agent.started_at = utc_now()
                agent.finished_at = None
                agent.returncode = None
                agent.pid = process.pid
                agent.log_path = str(log_path)
                agent.last_error = None
                self._write_agents(agents)
                return agent
        raise KeyError(agent_id)

    def stop_agent(self, agent_id: str, *, wait_timeout: float = 2.0) -> AgentRecord:
        with self._lock:
            agents = self._read_agents()
            for agent in agents:
                if agent.agent_id != agent_id:
                    continue
                process = self._processes.get(agent.agent_id)
                if process is None:
                    if agent.status == "running":
                        agent.status = "disconnected"
                        agent.last_error = "The server no longer controls this process."
                        self._write_agents(agents)
                    return agent
                process.terminate()
                try:
                    process.wait(timeout=wait_timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=wait_timeout)
                self._refresh_agent(agent)
                self._write_agents(agents)
                return agent
        raise KeyError(agent_id)

    def _refresh_agent(self, agent: AgentRecord) -> None:
        process = self._processes.get(agent.agent_id)
        if process is None:
            return
        returncode = process.poll()
        if returncode is None:
            agent.status = "running"
            return
        agent.returncode = returncode
        agent.finished_at = utc_now()
        agent.status = "completed" if returncode == 0 else "failed"
        self._processes.pop(agent.agent_id, None)
        handle = self._log_handles.pop(agent.agent_id, None)
        if handle is not None:
            handle.close()

    def wait_for_agent(self, agent_id: str, timeout: float = 5.0) -> AgentRecord:
        deadline = time.time() + timeout
        while time.time() < deadline:
            agent = self.get_agent(agent_id)
            if agent is None:
                raise KeyError(agent_id)
            if agent.status not in {"queued", "running"}:
                return agent
            time.sleep(0.05)
        agent = self.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        return agent

    def _validate_workdir(self, workdir: Path) -> None:
        if workdir == self.project_root:
            return
        if self.project_root not in workdir.parents:
            raise ValueError("workdir must stay within the configured project root")


def _find_queue(queues: list[QueueRecord], queue_id: str) -> QueueRecord:
    for queue in queues:
        if queue.queue_id == queue_id:
            return queue
    raise KeyError(queue_id)


def _find_task(queue: QueueRecord, task_id: str) -> TaskRecord:
    for task in queue.tasks:
        if task.task_id == task_id:
            return task
    raise KeyError(task_id)


def _normalize_task_status(status: str) -> str:
    candidate = status.strip().lower() or "pending"
    if candidate not in {"pending", "in_progress", "done", "blocked"}:
        raise ValueError("status must be one of: pending, in_progress, done, blocked")
    return candidate


__all__ = [
    "AgentManager",
    "AgentRecord",
    "QueueRecord",
    "StateStore",
    "TaskQueueManager",
    "TaskRecord",
    "utc_now",
]
