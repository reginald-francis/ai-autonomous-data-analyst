"""Tests for ingestion.cli - flags in, pipeline calls and exit codes out.

The pipeline and the config are mocked: these tests are about the CLI's own job
(turning flags into calls, results into output, and failures into exit codes),
not about downloading or converting anything.
"""

import types

import pytest

from ingestion import cli, config
from ingestion.sources.sec_fsds_pipeline import QuarterOutcome

UA = "Test Agent test@example.com"
LAKE = "LAKE"
BASE = ["--source", "sec_fsds"]


@pytest.fixture
def wiring(mocker):
    """Stand-ins for the config and the two pipeline entry points."""
    return types.SimpleNamespace(
        user_agent=mocker.patch("ingestion.config.get_sec_user_agent", return_value=UA),
        lake_root=mocker.patch("ingestion.config.get_lake_root", return_value=LAKE),
        ingest=mocker.patch(
            "ingestion.sources.sec_fsds_pipeline.run_ingest",
            return_value=[QuarterOutcome("2026q2", "ingested")],
        ),
        check=mocker.patch(
            "ingestion.sources.sec_fsds_pipeline.run_check",
            return_value=[QuarterOutcome("2026q2", "up_to_date")],
        ),
    )


class TestDispatch:
    def test_single_quarter_runs_ingest(self, wiring):
        code = cli.main(BASE + ["--quarter", "2026q2"])

        assert code == 0
        wiring.ingest.assert_called_once_with(["2026q2"], LAKE, UA, force=False)
        wiring.check.assert_not_called()

    def test_range_is_expanded_into_every_quarter(self, wiring):
        cli.main(BASE + ["--from", "2025q4", "--to", "2026q1"])

        wiring.ingest.assert_called_once_with(["2025q4", "2026q1"], LAKE, UA, force=False)

    def test_check_runs_check_and_never_ingest(self, wiring):
        code = cli.main(BASE + ["--quarter", "2026q2", "--check"])

        assert code == 0
        wiring.check.assert_called_once_with(["2026q2"], LAKE, UA)
        wiring.ingest.assert_not_called()

    def test_force_is_passed_to_ingest(self, wiring):
        cli.main(BASE + ["--quarter", "2026q2", "--force"])

        wiring.ingest.assert_called_once_with(["2026q2"], LAKE, UA, force=True)


class TestUsageErrors:
    """Bad flags exit with code 2 and a message, before any work starts."""

    @pytest.mark.parametrize(
        "argv, message",
        [
            ([], "required"),
            (["--source", "other", "--quarter", "2026q2"], "invalid choice"),
            (BASE, "give --quarter"),
            (BASE + ["--quarter", "2026q2", "--from", "2025q1", "--to", "2026q1"], "not both"),
            (BASE + ["--from", "2025q1"], "give --quarter"),
            (BASE + ["--to", "2025q1"], "give --quarter"),
            (BASE + ["--quarter", "2026q5"], "Invalid quarter"),
            (BASE + ["--from", "2026q2", "--to", "2025q4"], "is after"),
            (BASE + ["--quarter", "2026q2", "--check", "--force"], "no effect"),
        ],
        ids=[
            "missing-source", "unknown-source", "no-quarter-given", "quarter-and-range",
            "from-only", "to-only", "malformed-quarter", "reversed-range", "check-with-force",
        ],
    )
    def test_exits_with_code_2_and_does_no_work(self, wiring, capsys, argv, message):
        with pytest.raises(SystemExit) as exc_info:
            cli.main(argv)

        assert exc_info.value.code == 2
        assert message in capsys.readouterr().err
        wiring.ingest.assert_not_called()
        wiring.check.assert_not_called()


class TestExitCodes:
    def test_all_succeeded_exits_0(self, wiring):
        wiring.ingest.return_value = [
            QuarterOutcome("2026q1", "ingested"),
            QuarterOutcome("2026q2", "unchanged"),
        ]
        assert cli.main(BASE + ["--from", "2026q1", "--to", "2026q2"]) == 0

    def test_any_failed_quarter_exits_1(self, wiring):
        wiring.ingest.return_value = [
            QuarterOutcome("2026q1", "ingested"),
            QuarterOutcome("2026q2", "failed", "HTTPError: 404"),
        ]
        assert cli.main(BASE + ["--from", "2026q1", "--to", "2026q2"]) == 1

    def test_check_exits_0_even_when_work_is_pending(self, wiring):
        wiring.check.return_value = [QuarterOutcome("2026q2", "would_ingest", "never ingested")]
        assert cli.main(BASE + ["--quarter", "2026q2", "--check"]) == 0

    def test_missing_user_agent_exits_2_and_does_no_work(self, wiring, capsys):
        wiring.user_agent.side_effect = config.ConfigError("SEC_USER_AGENT is required")

        code = cli.main(BASE + ["--quarter", "2026q2"])

        assert code == 2
        err = capsys.readouterr().err
        assert "error:" in err
        assert "SEC_USER_AGENT" in err
        wiring.ingest.assert_not_called()


class TestOutput:
    def test_prints_one_line_per_quarter_then_a_summary(self, wiring, capsys):
        wiring.ingest.return_value = [
            QuarterOutcome("2026q1", "ingested", "", {"sub": 6123, "num": 1234567}),
            QuarterOutcome("2026q2", "unchanged", "nothing downloaded"),
        ]

        cli.main(BASE + ["--from", "2026q1", "--to", "2026q2"])

        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("2026q1")
        assert lines[1].startswith("2026q2")
        assert lines[2] == "2 quarters: 1 ingested, 1 unchanged"

    def test_format_outcome_shows_row_counts_with_thousands_separators(self):
        line = cli._format_outcome(
            QuarterOutcome("2026q2", "ingested", "", {"sub": 6123, "num": 1234567})
        )

        assert line.startswith("2026q2")
        assert "ingested" in line
        assert "sub=6,123" in line
        assert "num=1,234,567" in line

    def test_format_outcome_lists_tables_in_table_order_whatever_order_they_arrive_in(self):
        # A manifest stores its keys sorted, so counts read back from one arrive
        # alphabetical (num, pre, sub, tag) - the output should still be table order.
        line = cli._format_outcome(
            QuarterOutcome("2026q2", "unchanged", "", {"num": 3, "pre": 4, "sub": 1, "tag": 2})
        )

        assert line.index("sub=") < line.index("num=") < line.index("tag=") < line.index("pre=")

    def test_format_outcome_keeps_a_table_it_does_not_recognise_at_the_end(self):
        line = cli._format_outcome(
            QuarterOutcome("2026q2", "ingested", "", {"extra": 5, "sub": 1})
        )

        assert line.index("sub=1") < line.index("extra=5")

    def test_format_outcome_for_a_failure_shows_the_detail_and_no_counts(self):
        line = cli._format_outcome(QuarterOutcome("2026q3", "failed", "HTTPError: 404"))

        assert "failed" in line
        assert "HTTPError: 404" in line
        assert "=" not in line

    def test_summary_uses_the_singular_for_one_quarter(self):
        assert cli._summary([QuarterOutcome("2026q2", "ingested")]) == "1 quarter: 1 ingested"

    def test_summary_counts_each_status(self):
        outcomes = [
            QuarterOutcome("2026q1", "ingested"),
            QuarterOutcome("2026q2", "failed"),
            QuarterOutcome("2026q3", "ingested"),
        ]
        assert cli._summary(outcomes) == "3 quarters: 2 ingested, 1 failed"
