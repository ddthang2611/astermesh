from astermesh.graph.filters import is_test_path as _global_is_test_path
from astermesh.languages.base import LanguagePlugin
from astermesh.languages.js_ts_plugin import JsTsWebLanguagePlugin
from astermesh.languages.python_plugin import PythonLanguagePlugin


# central language plugin registry
class LanguageRegistry:
    """Registry mapping extensions and families."""

    def __init__(self) -> None:
        self._plugins: list[LanguagePlugin] = []
        self._by_ext: dict[str, LanguagePlugin] = {}
        self._by_family: dict[str, LanguagePlugin] = {}

    def register(self, plugin: LanguagePlugin) -> None:
        """Register language plugin by extensions and family."""
        self._plugins.append(plugin)
        if plugin.language_family:
            self._by_family[plugin.language_family] = plugin
        for ext in plugin.extensions:
            self._by_ext[ext.lower()] = plugin

    def plugin_for_extension(self, ext: str) -> LanguagePlugin | None:
        """Lookup plugin handling file extension."""
        return self._by_ext.get(ext.lower())

    def plugin_for_family(self, family: str) -> LanguagePlugin | None:
        """Lookup plugin handling language family."""
        return self._by_family.get(family)

    def plugins(self) -> tuple[LanguagePlugin, ...]:
        """Return all registered plugins."""
        return tuple(self._plugins)

    @property
    def supported_extensions(self) -> frozenset[str]:
        """Return all supported file extensions."""
        return frozenset(self._by_ext.keys())

    def all_config_watch_files(self) -> frozenset[str]:
        """Return union of watched config filenames."""
        out: set[str] = set()
        for plugin in self._plugins:
            out.update(plugin.config_watch_files)
        return frozenset(out)

    def all_ignore_dirs(self) -> frozenset[str]:
        """Return union of plugin ignore dirs."""
        out: set[str] = set()
        for plugin in self._plugins:
            out.update(plugin.ignore_dirs)
        return frozenset(out)

    def is_test_path(self, rel_path: str, family: str | None = None) -> bool:
        """Check test path for family or all."""
        if family is not None:
            plugin = self._by_family.get(family)
            if plugin is not None:
                return plugin.is_test_path(rel_path)
            return _global_is_test_path(rel_path)
        if any(plugin.is_test_path(rel_path) for plugin in self._plugins):
            return True
        return _global_is_test_path(rel_path)


def default_registry() -> LanguageRegistry:
    """Build registry with available language plugins."""
    registry = LanguageRegistry()
    for plugin in (PythonLanguagePlugin(), JsTsWebLanguagePlugin()):
        if plugin.is_available():
            registry.register(plugin)

    try:
        from astermesh.languages.java.plugin import JavaLanguagePlugin

        java_plugin = JavaLanguagePlugin()
        if java_plugin.is_available():
            registry.register(java_plugin)
    except ImportError:
        pass

    return registry
