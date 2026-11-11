import threading
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.watcher import GraphEventHandler


def test_watcher_event_handler(tmp_path: Path):
    # arrange
    builder = GraphBuilder(tmp_path)
    builder.build_full()
    callback_fired = False

    def on_update():
        nonlocal callback_fired
        callback_fired = True

    handler = GraphEventHandler(builder, on_updated=on_update, debounce_sec=0.01)

    # act: simulate creating a file
    fpath = tmp_path / "mod.py"
    fpath.write_text("def worker(): pass\n", encoding="utf-8")
    event_created = FileCreatedEvent(str(fpath))
    handler.on_any_event(event_created)

    # assert: builder updated
    assert builder.graph.has_node("mod.worker")

    # act: simulate deleting the file
    fpath.unlink()
    event_deleted = FileDeletedEvent(str(fpath))
    handler.on_any_event(event_deleted)

    # assert: builder removed
    assert not builder.graph.has_node("mod.worker")


# kiem thu su kien moved, reload tsconfig, ignore node_modules va debounce
def test_watcher_moved_tsconfig_ignore_and_debounce(tmp_path: Path):
    (tmp_path / "src").mkdir()
    old_f = tmp_path / "src/OldComp.tsx"
    old_f.write_text("export const OldComp = () => <div />;", encoding="utf-8")

    builder = GraphBuilder(tmp_path)
    builder.build_full()
    assert builder.graph.has_node("src.OldComp.OldComp")

    fired = threading.Event()
    handler = GraphEventHandler(builder, on_updated=fired.set, debounce_sec=0.02)

    new_f = tmp_path / "src/NewComp.tsx"
    old_f.rename(new_f)
    new_f.write_text("export const NewComp = () => <span />;", encoding="utf-8")
    handler.on_any_event(FileMovedEvent(str(old_f), str(new_f)))

    assert not builder.graph.has_node("src.OldComp.OldComp")
    assert builder.graph.has_node("src.NewComp.NewComp")
    assert fired.wait(timeout=1.0)

    nm_dir = tmp_path / "node_modules/pkg"
    nm_dir.mkdir(parents=True)
    nm_file = nm_dir / "index.js"
    nm_file.write_text("function skip() {}", encoding="utf-8")
    handler.on_any_event(FileCreatedEvent(str(nm_file)))
    assert not builder.graph.has_node("node_modules.pkg.index.skip")

    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text('{"compilerOptions": {"paths": {"@/*": ["src/*"]}}}', encoding="utf-8")
    handler.on_any_event(FileModifiedEvent(str(tsconfig)))
    assert builder.resolver._has_aliases is True
