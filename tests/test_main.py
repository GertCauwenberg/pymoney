"""The ``python -m pymoney`` entry point, which doubles as the image smoke test."""

from __future__ import annotations

import pytest

from pymoney import __version__
from pymoney.__main__ import _available, main


def test_reports_the_version(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert f"PyMoney {__version__}" in out


def test_lists_components_with_their_status(capsys):
    main([])
    out = capsys.readouterr().out
    assert "[ok     ] pymoney.core" in out
    assert "[ok     ] pymoney.api" in out


def test_says_how_to_run_what_exists(capsys):
    main([])
    out = capsys.readouterr().out
    assert "uvicorn pymoney.api:app" in out
    assert "pymoney.importers.kmymoney" in out


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_version_flag_prints_only_the_version(flag, capsys):
    assert main([flag]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_unknown_arguments_fall_through_to_the_inventory(capsys):
    """A diagnostic should never fail on input; it should just report."""
    assert main(["--whatever"]) == 0
    assert "PyMoney" in capsys.readouterr().out


def test_argv_defaults_to_sys_argv(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["pymoney", "--version"])
    assert main() == 0
    assert capsys.readouterr().out.strip() == __version__


class TestAvailable:
    def test_detects_an_installed_module(self):
        assert _available("pymoney.core")

    def test_detects_a_missing_module(self):
        assert not _available("pymoney.mobile")

    def test_a_missing_parent_package_is_not_an_error(self):
        assert not _available("pymoney.nope.deeper")

    def test_a_component_counts_as_present_only_if_it_imports(self):
        """They ship in the wheel but need their extras to work."""
        assert _available("pymoney.store") == _available("sqlalchemy")
        assert _available("pymoney.api") == _available("fastapi")
