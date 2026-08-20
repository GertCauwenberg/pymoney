"""The ``python -m pymoney`` entry point.

It is a trivial wrapper, but it is also what Docker's ``HEALTHCHECK`` calls,
so breaking it would ship a broken image.
"""

from __future__ import annotations

import pymoney
from pymoney.__main__ import main, self_check


class TestSelfCheck:
    def test_exits_zero_on_a_healthy_build(self, capsys):
        assert self_check() == 0
        out = capsys.readouterr().out
        assert f"pymoney {pymoney.__version__}: OK" in out

    def test_verbose_reports_detail(self, capsys):
        assert self_check(verbose=True) == 0
        out = capsys.readouterr().out
        assert "net worth" in out
        assert "revision" in out


class TestCli:
    def test_version_flag(self, capsys):
        assert main(["--version"]) == 0
        assert capsys.readouterr().out.strip() == pymoney.__version__

    def test_default_runs_the_self_check(self, capsys):
        assert main([]) == 0
        assert "OK" in capsys.readouterr().out

    def test_verbose_flag(self, capsys):
        assert main(["-v"]) == 0
        assert "net worth" in capsys.readouterr().out

    def test_unknown_flag_exits(self, capsys):
        import pytest

        with pytest.raises(SystemExit):
            main(["--nonsense"])


class TestSelfCheckFailure:
    def test_integrity_failure_returns_nonzero(self, capsys, monkeypatch):
        """If the domain ever loses its invariants, the smoke test must notice."""
        from pymoney.core.book import Book

        monkeypatch.setattr(Book, "check_integrity", lambda self: ("contrived failure",))
        assert self_check() == 1
        err = capsys.readouterr().err
        assert "integrity check failed" in err
        assert "contrived failure" in err
