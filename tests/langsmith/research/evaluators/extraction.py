"""
Lameh Intelligence (research) - response parsing
=================================================
Parses a board-analysis answer into the two things the deterministic
evaluators need: the provenance-tagged figures, and the structural blocks the
agent wraps its prose in.

The tag shape here is **not** the FS suite's
-----------------------------------------------
The FS suite parses `<calc>` and `<number>` tags carrying `rid` / `id` / `ops`
- identifiers into the financial-statement DB. Board-analysis answers use the
same `<number>` element with an entirely different attribute set, confirmed
against a live answer on 2026-08-23:

    <number document_id="24c0597d-..." type="board_analysis" value="17.5 million"
            raw="17.5" page="47" evidence_block_id="4a56ea3d-..."
            section="Key Business Metrics & Activity" company="شركة أسمنت اليمامة">17.5 million</number>

So provenance means something different: not "which row of which statement"
but "which document, which page, which evidence block". That is why this
module exists instead of importing fs/evaluators/extraction.py - sharing it
would have meant one required-attribute list that is wrong for both suites.

`value` and `raw` are also not redundant and not always in the same units:
the probe's `raw="17.5"` sits against a merge/tables cell of 17500000.0, with
the scale carried in the display text ("17.5 million"). Nothing here reconciles
them - see ground_truth.py for why this suite does not do value comparison.

Tags nest, so each is matched on its own
-----------------------------------------
The whole answer is wrapped in `<result>`, and `<number>` tags sit inside
`<data_table>` inside that. One generic "any tag" regex is therefore wrong and
was wrong in the first draft of this module: `<result>` matched first, its
non-greedy body ran to `</result>`, and `finditer` resumed *after* it - so
every nested tag in a real answer parsed as zero tags. Each tag name is now
scanned independently, which is nesting-proof because the scans never share a
cursor.
"""

import re

NUMBER_TAG = "number"
STRUCTURAL_TAGS = ("result", "data_table", "analysis", "key_finding")

_ATTR_RE = re.compile(r'(?P<key>[a-z_]+)\s*=\s*"(?P<value>[^"]*)"', re.IGNORECASE)

# What a board-analysis <number> must carry for a reader to check it. Chosen
# from the probe's own emitted attributes rather than invented: document_id +
# page + evidence_block_id is the triple that locates a figure in a source
# report, and `value` is what the tag claims independently of its display text.
#
# `section` and `company` are deliberately NOT required. Both were present on
# the probe, but neither is needed to find the figure, and gating on an
# attribute that carries no verification weight turns a formatting drift into
# a release blocker.
REQUIRED_NUMBER_ATTRS = ("document_id", "page", "evidence_block_id", "value")


def _tag_re(name):
    """Matches one named tag, innermost-friendly: the body excludes any
    further opening of the same tag, so same-name nesting (which the agent
    does not currently emit, but which would silently mis-parse) can't run one
    tag's body into the next one's."""
    return re.compile(rf"<{name}(?P<attrs>\s[^>]*?)?>(?P<text>(?:(?!<{name}[\s>]).)*?)</{name}>",
                      re.DOTALL | re.IGNORECASE)


def _parse_attrs(raw):
    return {m.group("key").lower(): m.group("value") for m in _ATTR_RE.finditer(raw or "")}


def find_tags(text, name):
    """Every instance of one tag, in document order, as {name, attrs, text}."""
    return [{"name": name,
             "attrs": _parse_attrs(m.group("attrs")),
             "text": (m.group("text") or "").strip()}
            for m in _tag_re(name).finditer(text or "")]


def number_tags(text):
    """The provenance-bearing figure tags."""
    return find_tags(text, NUMBER_TAG)


def structural_blocks(text):
    """The prose-structure tags, as {tag_name: [inner text, ...]}. Reported so
    an answer that emits no structure at all is visible - not because anything
    grades the markup for its own sake."""
    blocks = {}
    for name in STRUCTURAL_TAGS:
        found = find_tags(text, name)
        if found:
            blocks[name] = [t["text"] for t in found]
    return blocks


def missing_attrs(tag, required=REQUIRED_NUMBER_ATTRS):
    """Which required attributes this tag lacks or leaves empty. An attribute
    present but blank counts as missing: `page=""` locates nothing."""
    return [key for key in required if not (tag["attrs"].get(key) or "").strip()]


def untagged_number_candidates(text):
    """Figures in the prose that sit outside any `<number>` tag.

    Only `<number>` markup is stripped - not the structural tags, whose bodies
    *are* the prose. Deliberately crude and deliberately not a score: answers
    here are full of numbers that are not figures (meeting counts, ISO
    standard numbers, article numbers, ordinals), so this over-reports by
    design. It exists to give the hallucination judge a starting list, not to
    decide anything on its own.
    """
    stripped = _tag_re(NUMBER_TAG).sub(" ", text or "")
    # Structural tags: drop the delimiters, keep the prose inside them.
    stripped = re.sub(r"</?(?:%s)(?:\s[^>]*)?>" % "|".join(STRUCTURAL_TAGS), " ",
                      stripped, flags=re.IGNORECASE)
    # Dates and markdown table pipes are dense here and neither is a claimed
    # figure.
    stripped = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", stripped)
    return re.findall(r"(?<![\w.-])\d[\d,]*\.?\d*(?:\s*(?:million|billion|thousand|%))?", stripped)
