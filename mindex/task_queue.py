from __future__ import annotations

import copy
import json
from pathlib import Path
import threading
import uuid
from typing import Any

from mindex.logging_utils import utc_timestamp


DEFAULT_UI_CONFIG_NAME = "ui_config.json"


class QueueStoreError(ValueError):
    """Raised when a queue operation would violate the session rules."""


def default_ui_config(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    return {
        "server": {
            "host": "0.0.0.0",
            "port": 8000,
        },
        "auth": {
            "username": "admin",
            "password": "123456",
        },
        "storage": {
            "state_file": ".mindex/task_queues.json",
            "queue_log_dir": ".mindex/queue_logs",
        },
        "ui": {
            "title": "MindX Session Director",
        },
        "project_root": str(root),
    }


def merge_dicts(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def ensure_ui_config(
    project_root: Path | str,
    *,
    config_path: Path | str | None = None,
) -> tuple[Path, dict[str, Any]]:
    root = Path(project_root).resolve()
    resolved_path = Path(config_path).resolve() if config_path else (root / ".mindex" / DEFAULT_UI_CONFIG_NAME)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    defaults = default_ui_config(root)
    if not resolved_path.exists():
        resolved_path.write_text(json.dumps(defaults, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    loaded = json.loads(resolved_path.read_text(encoding="utf-8"))
    merged = merge_dicts(defaults, loaded)
    merged["project_root"] = str(root)

    if merged != loaded:
        resolved_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return resolved_path, merged


def resolve_storage_path(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _normalize_text(value: str | None, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise QueueStoreError(f"{field_name} is required.")
    return text


class QueueStore:
    def __init__(self, *, state_path: Path | str, queue_log_dir: Path | str) -> None:
        self.state_path = Path(state_path).resolve()
        self.queue_log_dir = Path(queue_log_dir).resolve()
        self._lock = threading.RLock()
        self._ensure_storage()

    @classmethod
    def from_config(cls, project_root: Path | str, config: dict[str, Any]) -> "QueueStore":
        root = Path(project_root).resolve()
        storage = config.get("storage", {})
        return cls(
            state_path=resolve_storage_path(root, storage.get("state_file", ".mindex/task_queues.json")),
            queue_log_dir=resolve_storage_path(root, storage.get("queue_log_dir", ".mindex/queue_logs")),
        )

    def _ensure_storage(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_log_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._write_state({"version": 1, "queues": []})

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": 1, "queues": []}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write_state(self, payload: dict[str, Any]) -> None:
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(self.state_path)

    def _queue_log_path(self, queue_id: str) -> Path:
        return self.queue_log_dir / f"{queue_id}.jsonl"

    def _append_log(self, queue_id: str, event_type: str, details: dict[str, Any]) -> None:
        payload = {
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "queue_id": queue_id,
            "timestamp": utc_timestamp(),
            "details": details,
        }
        with self._queue_log_path(queue_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def _load_logs(self, queue_id: str) -> list[dict[str, Any]]:
        path = self._queue_log_path(queue_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _find_queue(self, payload: dict[str, Any], queue_id: str) -> dict[str, Any]:
        for queue in payload["queues"]:
            if queue["id"] == queue_id:
                return queue
        raise QueueStoreError(f"Queue '{queue_id}' was not found.")

    def _find_task(self, queue: dict[str, Any], task_id: str) -> tuple[int, dict[str, Any]]:
        for index, task in enumerate(queue["tasks"]):
            if task["id"] == task_id:
                return index, task
        raise QueueStoreError(f"Task '{task_id}' was not found.")

    def _contiguous_completed_prefix(self, queue: dict[str, Any]) -> list[dict[str, Any]]:
        prefix: list[dict[str, Any]] = []
        for task in queue["tasks"]:
            if task["status"] != "completed":
                break
            prefix.append(task)
        return prefix

    def _next_pending_task(self, queue: dict[str, Any]) -> dict[str, Any] | None:
        for task in queue["tasks"]:
            if task["status"] == "pending":
                return task
        return None

    def _refresh_queue(self, queue: dict[str, Any], *, now: str | None = None) -> None:
        timestamp = now or utc_timestamp()
        next_task = self._next_pending_task(queue)
        queue["current_task_id"] = next_task["id"] if next_task else None
        queue["status"] = "completed" if queue["tasks"] and next_task is None else "active"
        queue["updated_at"] = timestamp
        if queue["status"] == "completed":
            if not queue.get("completed_at"):
                queue["completed_at"] = timestamp
        else:
            queue["completed_at"] = None

    def _task_summary(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": task["id"],
            "title": task["title"],
            "instructions": task["instructions"],
            "status": task["status"],
            "completed_at": task.get("completed_at"),
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        }

    def _queue_snapshot(self, queue: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(queue)
        snapshot["events"] = self._load_logs(queue["id"])
        snapshot["task_count"] = len(snapshot["tasks"])
        snapshot["completed_count"] = sum(1 for task in snapshot["tasks"] if task["status"] == "completed")
        snapshot["current_task"] = None
        if snapshot.get("current_task_id"):
            for task in snapshot["tasks"]:
                if task["id"] == snapshot["current_task_id"]:
                    snapshot["current_task"] = task
                    break
        return snapshot

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            queues = [self._queue_snapshot(queue) for queue in state["queues"]]
            queues.sort(key=lambda item: (item["status"] == "completed", item["created_at"]))
            return {
                "generated_at": utc_timestamp(),
                "version": state.get("version", 1),
                "queues": queues,
            }

    def create_queue(self, *, name: str, description: str = "") -> dict[str, Any]:
        timestamp = utc_timestamp()
        queue = {
            "id": f"queue-{uuid.uuid4().hex[:8]}",
            "name": _normalize_text(name, "Queue name"),
            "description": description.strip(),
            "tasks": [],
            "created_at": timestamp,
            "updated_at": timestamp,
            "status": "active",
            "completed_at": None,
            "current_task_id": None,
        }
        with self._lock:
            state = self._read_state()
            state["queues"].append(queue)
            self._write_state(state)
            self._append_log(queue["id"], "queue.created", {"name": queue["name"], "description": queue["description"]})
            return self._queue_snapshot(queue)

    def update_queue(self, queue_id: str, *, name: str | None = None, description: str | None = None) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            queue = self._find_queue(state, queue_id)
            changes: dict[str, Any] = {}
            if name is not None:
                queue["name"] = _normalize_text(name, "Queue name")
                changes["name"] = queue["name"]
            if description is not None:
                queue["description"] = description.strip()
                changes["description"] = queue["description"]
            queue["updated_at"] = utc_timestamp()
            self._write_state(state)
            if changes:
                self._append_log(queue_id, "queue.updated", changes)
            return self._queue_snapshot(queue)

    def add_task(self, queue_id: str, *, title: str, instructions: str = "") -> dict[str, Any]:
        timestamp = utc_timestamp()
        task = {
            "id": f"task-{uuid.uuid4().hex[:8]}",
            "title": _normalize_text(title, "Task title"),
            "instructions": instructions.strip(),
            "status": "pending",
            "created_at": timestamp,
            "updated_at": timestamp,
            "completed_at": None,
        }
        with self._lock:
            state = self._read_state()
            queue = self._find_queue(state, queue_id)
            queue["tasks"].append(task)
            self._refresh_queue(queue, now=timestamp)
            self._write_state(state)
            self._append_log(queue_id, "task.created", {"task": self._task_summary(task)})
            return self._queue_snapshot(queue)

    def update_task(
        self,
        queue_id: str,
        task_id: str,
        *,
        title: str | None = None,
        instructions: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            queue = self._find_queue(state, queue_id)
            _, task = self._find_task(queue, task_id)
            changes: dict[str, Any] = {}
            if title is not None:
                task["title"] = _normalize_text(title, "Task title")
                changes["title"] = task["title"]
            if instructions is not None:
                task["instructions"] = instructions.strip()
                changes["instructions"] = task["instructions"]
            task["updated_at"] = utc_timestamp()
            self._refresh_queue(queue, now=task["updated_at"])
            self._write_state(state)
            if changes:
                self._append_log(queue_id, "task.updated", {"task_id": task_id, "changes": changes})
            return self._queue_snapshot(queue)

    def delete_task(self, queue_id: str, task_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            queue = self._find_queue(state, queue_id)
            index, task = self._find_task(queue, task_id)
            queue["tasks"].pop(index)
            self._refresh_queue(queue)
            self._write_state(state)
            self._append_log(queue_id, "task.deleted", {"task": self._task_summary(task)})
            return self._queue_snapshot(queue)

    def reorder_tasks(self, queue_id: str, task_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            queue = self._find_queue(state, queue_id)
            existing_ids = [task["id"] for task in queue["tasks"]]
            if sorted(existing_ids) != sorted(task_ids):
                raise QueueStoreError("Reorder requests must include every task exactly once.")

            task_map = {task["id"]: task for task in queue["tasks"]}
            reordered = [task_map[task_id] for task_id in task_ids]
            seen_pending = False
            for task in reordered:
                if task["status"] == "pending":
                    seen_pending = True
                elif seen_pending:
                    raise QueueStoreError("Completed tasks must stay ahead of pending tasks in the session timeline.")

            queue["tasks"] = reordered
            queue["updated_at"] = utc_timestamp()
            self._refresh_queue(queue, now=queue["updated_at"])
            self._write_state(state)
            self._append_log(queue_id, "queue.reordered", {"task_ids": task_ids})
            return self._queue_snapshot(queue)

    def set_task_completion(self, queue_id: str, task_id: str, *, completed: bool) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            queue = self._find_queue(state, queue_id)
            index, task = self._find_task(queue, task_id)
            timestamp = utc_timestamp()

            if completed:
                next_task = self._next_pending_task(queue)
                if next_task is None:
                    raise QueueStoreError("This queue is already complete.")
                if next_task["id"] != task_id:
                    raise QueueStoreError(
                        "Tasks must be completed in order. Reorder the queue if a different task should run next."
                    )
                task["status"] = "completed"
                task["completed_at"] = timestamp
                event_type = "task.completed"
            else:
                prefix = self._contiguous_completed_prefix(queue)
                if not prefix or prefix[-1]["id"] != task_id or index != len(prefix) - 1:
                    raise QueueStoreError("Only the most recently completed task can be reopened.")
                task["status"] = "pending"
                task["completed_at"] = None
                event_type = "task.reopened"

            task["updated_at"] = timestamp
            self._refresh_queue(queue, now=timestamp)
            self._write_state(state)
            self._append_log(
                queue_id,
                event_type,
                {
                    "task_id": task_id,
                    "title": task["title"],
                    "status": task["status"],
                },
            )
            if queue["status"] == "completed":
                self._append_log(
                    queue_id,
                    "queue.completed",
                    {
                        "completed_count": len(queue["tasks"]),
                        "completed_at": queue["completed_at"],
                    },
                )
            return self._queue_snapshot(queue)
