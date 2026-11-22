import os
import threading
import unicodedata
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.filters import is_ignored_dir, is_ignored_file, is_saved_webpage_dir

_HANDLED_EVENT_TYPES: frozenset[str] = frozenset({
    "created",
    "modified",
    "deleted",
    "moved",
})
_CONFIG_FILES: frozenset[str] = frozenset({"tsconfig.json", "jsconfig.json"})


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
        return self.builder.parser.supports(
            fpath.suffix.lower()
        ) or self.builder.satellite.supports(fpath)

    # reload resolver config under builder lock
    def _maybe_reload_config(self, *paths: Path) -> bool:
        if any(p.name in _CONFIG_FILES for p in paths):
            with self.builder.lock:
                self.builder.resolver.reload_config()
            return True
        return False

    def _is_in_saved_webpage_dir(self, rel: str, is_dir: bool) -> bool:
        parts = rel.split("/")
        limit = len(parts) if is_dir else len(parts) - 1
        for idx in range(limit):
            seg = parts[idx]
            if not seg.lower().endswith("_files"):
                continue
            parent_dir = self.builder.root_path.joinpath(*parts[:idx])
            try:
                siblings = set(os.listdir(parent_dir))
            except OSError:
                continue
            if is_saved_webpage_dir(seg, siblings):
                return True
        return False

    def _relative_to_root(self, path: Path) -> str | None:
        try:
            raw_rel = path.resolve().relative_to(self.builder.root_path).as_posix()
        except (OSError, ValueError):
            return None
        rel = unicodedata.normalize("NFC", raw_rel)
        if is_ignored_dir(rel):
            return None
        is_dir = path.is_dir()
        if self.builder.is_excluded(rel, is_dir=is_dir):
            return None
        if self._is_in_saved_webpage_dir(rel, is_dir=is_dir):
            return None
        return rel

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in _HANDLED_EVENT_TYPES:
            return

        src_path = Path(os.fsdecode(event.src_path))
        rel_path = self._relative_to_root(src_path)
        if rel_path is None:
            return

        if event.event_type == "deleted":
            reloaded = self._maybe_reload_config(src_path)
            self.builder.remove_path(rel_path)
            if reloaded or self._is_supported_file(src_path):
                self._schedule_callback()
        elif event.event_type in ("created", "modified"):
            reloaded = self._maybe_reload_config(src_path)
            if not event.is_directory and self._is_supported_file(src_path):
                self.builder.update_file(src_path)
                self._schedule_callback()
            elif event.is_directory and event.event_type == "created":
                with self.builder.lock:
                    self.builder.ensure_folder(src_path)
                self._schedule_callback()
            elif reloaded:
                self._schedule_callback()
        elif event.event_type == "moved":
            raw_dest = getattr(event, "dest_path", "")
            dest_str = os.fsdecode(raw_dest) if raw_dest else ""
            dest_path = Path(dest_str) if dest_str else None
            dest_rel = self._relative_to_root(dest_path) if dest_path is not None else None
            if dest_path is not None and dest_rel is not None:
                reloaded = self._maybe_reload_config(src_path, dest_path)
            else:
                reloaded = self._maybe_reload_config(src_path)

            self.builder.remove_path(rel_path)
            if (
                dest_path is not None
                and dest_rel is not None
                and dest_path.exists()
                and not event.is_directory
                and self._is_supported_file(dest_path)
            ):
                self.builder.update_file(dest_path)
            if reloaded or self._is_supported_file(src_path) or dest_rel is not None:
                self._schedule_callback()

    def cancel_timer(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

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
        self._started = False

    def start(self) -> bool:
        if self._started:
            return True
        try:
            self.observer.schedule(self.handler, str(self.builder.root_path), recursive=True)
            self.observer.daemon = True
            self.observer.start()
            self._started = True
            return True
        except (OSError, RuntimeError):
            try:
                self.observer.unschedule_all()
            except (OSError, RuntimeError):
                pass
            return False

    def stop(self) -> None:
        self.handler.cancel_timer()
        if not self._started:
            return
        self._started = False
        try:
            self.observer.unschedule_all()
            self.observer.stop()
            if self.observer.is_alive():
                self.observer.join(timeout=2.0)
        except (OSError, RuntimeError):
            return
