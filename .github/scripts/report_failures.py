"""Turn a pytest JUnit report into GitHub annotations and a job summary.

The raw job log needs a click-through and a scroll; this puts the failing
test names and the first lines of each failure on the pull request itself.
Never fails the job on its own — pytest's exit code does that.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET  # noqa: S405 — parses our own pytest output
from pathlib import Path

MAX_ANNOTATIONS = 9  # GitHub shows at most 10 error annotations per step
MAX_LINES = 12


def _escape(text: str) -> str:
    """Escape a message for a workflow command."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    """Escape a property value (such as ``title``): ``:`` and ``,`` are syntax there."""
    return _escape(text).replace(":", "%3A").replace(",", "%2C")


def main(report: str) -> int:
    path = Path(report)
    if not path.exists():
        print(f"::warning::no test report at {report}; pytest did not get as far as writing one")
        return 0

    root = ET.parse(path).getroot()  # noqa: S314 — our own pytest output
    cases = list(root.iter("testcase"))
    failures: list[tuple[str, str, str]] = []
    skipped = 0
    for case in cases:
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("skipped") is not None:
            skipped += 1
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is not None:
                detail = (node.get("message") or "") + "\n" + (node.text or "")
                failures.append((name, kind, detail.strip()))

    passed = len(cases) - len(failures) - skipped
    headline = f"{passed} passed, {len(failures)} failed, {skipped} skipped"
    print(headline)
    print(f"::notice title=Test results::{_escape(headline)}")

    for name, kind, detail in failures[:MAX_ANNOTATIONS]:
        tail = "\n".join(detail.splitlines()[-MAX_LINES:])
        print(f"::error title={_escape_property(name)}::{_escape(f'{kind}: {tail}')}")
    if len(failures) > MAX_ANNOTATIONS:
        rest = ", ".join(n for n, _k, _d in failures[MAX_ANNOTATIONS:])
        print(f"::error title=More failures::{_escape(rest)}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {headline}\n\n")
            for name, kind, detail in failures:
                tail = "\n".join(detail.splitlines()[-MAX_LINES:])
                fh.write(f"<details><summary><code>{name}</code> ({kind})</summary>\n\n")
                fh.write(f"```\n{tail}\n```\n\n</details>\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "report.xml"))
