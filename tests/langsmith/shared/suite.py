"""
Lameh Intelligence - suite descriptor
======================================
One object describing everything the shared machinery (build_dataset.py,
report/build_report.py) needs to know about a suite it is running for, so
neither of those modules has to import a specific suite's config or branch on
which suite it is.

Before this existed, build_report.py imported the FS evaluator keys at module
scope and hardcoded a "By Sector" breakdown. Both are FS facts: the research
suite grades one company across many board-analysis tables, where a sector
column is a single repeated value and the useful slice is the *kind of trap* a
prompt sets. Rather than teach one report about two suites, the report now
renders whatever a SuiteSpec hands it.

A suite defines its spec in its own config.py as `REPORT_SPEC`.
"""


class SuiteSpec:
    """Immutable description of one eval suite.

    name            short slug, e.g. "fs" - used in CLI --suite and messages
    title           report heading, e.g. "Financial Statements"
    dataset_name    the LangSmith dataset (see the rename caveat in AGENT.md:
                    changing this value does NOT rename anything, it points at
                    a different name and will happily create an empty one)
    experiment_suite  middle segment of the experiment name, e.g.
                    "intelligence-fs"
    prompt_set_path the suite's prompt_set.json
    dimension_keys  evaluator keys in report column order
    dimension_labels  key -> human column header
    gated_keys      which dimensions block release
    seconds_keys    which dimensions are durations rather than 0-1 rates
    thresholds      key -> gate value
    group_by        breakdown tables, as (section title, column label,
                    metadata field). Empty tuple renders none.
    """

    def __init__(self, name, title, dataset_name, experiment_suite, prompt_set_path,
                 dimension_keys, dimension_labels, gated_keys, seconds_keys,
                 thresholds, group_by=()):
        self.name = name
        self.title = title
        self.dataset_name = dataset_name
        self.experiment_suite = experiment_suite
        self.prompt_set_path = prompt_set_path
        self.dimension_keys = tuple(dimension_keys)
        self.dimension_labels = dict(dimension_labels)
        self.gated_keys = tuple(gated_keys)
        self.seconds_keys = tuple(seconds_keys)
        self.thresholds = dict(thresholds)
        self.group_by = tuple(group_by)
        missing = [k for k in self.dimension_keys if k not in self.dimension_labels]
        if missing:
            raise ValueError(f"SuiteSpec {name!r}: dimension_keys without a label: {missing}")
        ungated = [k for k in self.gated_keys if k not in self.thresholds]
        if ungated:
            raise ValueError(f"SuiteSpec {name!r}: gated keys without a threshold: {ungated}")
