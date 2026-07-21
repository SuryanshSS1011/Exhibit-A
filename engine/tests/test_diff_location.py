from pathlib import Path

from exhibit_a.executor.base import ExecOutcome
from exhibit_a.verdict.diff_location import changed_line_map, traceback_touches_changed_lines


def test_changed_line_map_records_target_side_lines(tmp_path: Path):
    base = tmp_path / "base"
    target = tmp_path / "target"
    base.mkdir()
    target.mkdir()
    (base / "service.py").write_text("def value():\n    return 1\n")
    (target / "service.py").write_text("def value():\n    return 2\n")

    assert changed_line_map(str(base), str(target)) == {"service.py": {2}}


def test_traceback_path_can_be_absolute_container_path():
    outcome = ExecOutcome(
        exit_code=1,
        stdout='  File "/work/pkg/service.py", line 9, in value\nE   ValueError: bad\n',
        stderr="",
    )

    assert traceback_touches_changed_lines(outcome, {"pkg/service.py": {9}})
