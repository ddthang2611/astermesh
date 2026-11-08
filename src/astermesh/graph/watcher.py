import threading
from pathlib import Path
from typing import Callable, Optional
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from astermesh.graph.builder import GraphBuilder, IGNORE_DIRS


# xu ly su kien thay doi tep va thu muc
class GraphEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        builder: GraphBuilder,
        on_updated: Optional[Callable[[], None]] = None,
        debounce_sec: float = 0.3,
    ) -> None:
        self.builder = builder
        self.on_updated = on_updated
        self.debounce_sec = debounce_sec
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def _is_ignored(self, path_str: str) -> bool:
        parts = Path(path_str).parts
        return any(p in IGNORE_DIRS or p.startswith(".") for p in parts)

    def on_any_event(self, event: FileSystemEvent) -> None:
        if self._is_ignored(event.src_path):
            return

        src_path = Path(event.src_path)
        try:
            rel_path = src_path.resolve().relative_to(self.builder.root_path).as_posix()
        except ValueError:
            return

        if event.event_type == "deleted":
            self.builder.remove_path(rel_path)
            self._schedule_callback()
        elif event.event_type in ("created", "modified"):
            if not event.is_directory and self.builder.parser.supports(src_path.suffix.lower()):
                self.builder.update_file(src_path)
                self._schedule_callback()
            elif event.is_directory and event.event_type == "created":
                with self.builder.lock:
                    self.builder._ensure_folder_hierarchy(src_path)
                self._schedule_callback()
        elif event.event_type == "moved":
            self.builder.remove_path(rel_path)
            dest_path = Path(getattr(event, "dest_path", ""))
            if dest_path.exists() and not event.is_directory:
                self.builder.update_file(dest_path)
            self._schedule_callback()

    def _schedule_callback(self) -> None:
        if not self.on_updated:
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_sec, self.on_updated)
            self._timer.daemon = True
            self._timer.start()


# quan ly tien trinh watchdog
class WorkspaceWatcher:
    def __init__(
        self,
        builder: GraphBuilder,
        on_updated: Optional[Callable[[], None]] = None,
        debounce_sec: float = 0.3,
    ) -> None:
        self.builder = builder
        self.observer = Observer()
        self.handler = GraphEventHandler(
            builder, on_updated=on_updated, debounce_sec=debounce_sec
        )

    def start(self) -> None:
        self.observer.schedule(self.handler, str(self.builder.root_path), recursive=True)
        self.observer.daemon = True
        self.observer.start()

    def stop(self) -> None:
        self.observer.stop()
        self.observer.join(timeout=2.0)
