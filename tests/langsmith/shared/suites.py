"""
Lameh Intelligence - suite registry
====================================
Maps a suite slug ("fs", "research") to its SuiteSpec, so the shared tools
(build_dataset.py, report/build_report.py) can take a --suite flag without
importing either suite's config directly.

Two details are deliberate:

- **Configs are loaded lazily, by file path.** Both suites name their config
  module `config` - that name means "this suite's config" throughout the tree,
  which is why the shared one is `env_config`. A plain `import config` would
  still return whichever suite landed in sys.modules first and silently hand
  the wrong dataset name to the second, so each is loaded from an explicit
  path under a unique module name.
- **Nothing suite-specific is imported at module scope.** Each config reaches
  for its own ground-truth URLs; a `--suite research` run has no business
  failing because the FS config is misconfigured, or the reverse.
"""

import importlib.util
import sys
from pathlib import Path

_LANGSMITH_ROOT = Path(__file__).resolve().parent.parent

SUITE_NAMES = ("fs", "research")


def suite_dir(name):
    return _LANGSMITH_ROOT / name


def load_suite(name):
    """Slug -> SuiteSpec. Raises ValueError on an unknown slug so a typo in
    --suite names the valid options rather than failing later as an empty
    dataset lookup."""
    if name not in SUITE_NAMES:
        raise ValueError(f"Unknown suite {name!r}. Valid suites: {', '.join(SUITE_NAMES)}")

    module_name = f"lameh_suite_{name}_config"
    if module_name in sys.modules:
        return sys.modules[module_name].REPORT_SPEC

    # The suite's own directory and shared/ go on the path first: its config
    # imports `suite` and its evaluator modules by plain name, following the
    # same sys.path convention as the rest of the tree rather than packaging
    # this as an installable.
    for path in (str(_LANGSMITH_ROOT / "shared"), str(suite_dir(name))):
        if path not in sys.path:
            sys.path.insert(0, path)

    loader_spec = importlib.util.spec_from_file_location(module_name, suite_dir(name) / "config.py")
    module = importlib.util.module_from_spec(loader_spec)
    sys.modules[module_name] = module
    loader_spec.loader.exec_module(module)
    return module.REPORT_SPEC
