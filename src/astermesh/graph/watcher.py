import os
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.filters import is_ignored_dir, is_ignored_file


# file system change event handler
class GraphEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        builder: GraphBuilder,
        on_updated: Callable[[], None] | None = None,
        debounce_sec: float = 0.3,
    ) -> None:
        self.builder = builder
        self.on_updated = on_updated
        self.debounce_sec = debounce_sec
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _is_supported_file(self, fpath: Path) -> bool:
        if is_ignored_file(fpath.name):
            return False
        return self.builder.parser.supports(fpath.suffix.lower()) or self.builder.satellite.supports(
            fpath
        )

    def on_any_event(self, event: FileSystemEvent) -> None:
        src_path_str = os.fsdecode(event.src_path)
        if is_ignored_dir(src_path_str):
            return

        src_path = Path(src_path_str)
        try:
            rel_path = src_path.resolve().relative_to(self.builder.root_path).as_posix()
        except ValueError:
            return

        if src_path.name in ("tsconfig.json", "jsconfig.json"):
            self.builder.resolver.reload_config()

        if event.event_type == "deleted":
            self.builder.remove_path(rel_path)
            self._schedule_callback()
        elif event.event_type in ("created", "modified"):
            if not event.is_directory and self._is_supported_file(src_path):
                self.builder.update_file(src_path)
                self._schedule_callback()
            elif event.is_directory and event.event_type == "created":
                with self.builder.lock:
                    self.builder.ensure_folder(src_path)
                self._schedule_callback()
        elif event.event_type == "moved":
            self.builder.remove_path(rel_path)
            raw_dest = getattr(event, "dest_path", "")
            dest_str = os.fsdecode(raw_dest) if raw_dest else ""
            if dest_str:
                dest_path = Path(dest_str)
                if dest_path.exists() and not event.is_directory and self._is_supported_file(dest_path):
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


# workspace watchdog observer manager
class WorkspaceWatcher:
    def __init__(
        self,
        builder: GraphBuilder,
        on_updated: Callable[[], None] | None = None,
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
