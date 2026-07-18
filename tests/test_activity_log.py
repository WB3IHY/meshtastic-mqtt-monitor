"""Unit tests for MeshtasticMonitor's activity log writing, rotation, and replay."""

from src.config import MonitorConfig, MQTTConfig
from src.monitor import MeshtasticMonitor


class TestActivityLog:
    """Test activity log writing, rotation, and startup replay."""

    def test_write_to_log_appends_timestamped_line(self, tmp_path):
        config = MonitorConfig(mqtt=MQTTConfig(host="test.broker.com"), topic="msh/test/#")
        config.log.path = str(tmp_path / "activity.log")
        mon = MeshtasticMonitor(config)
        mon._open_log()

        mon._write_to_log("[POSITION] hello")
        mon._log_file.close()
        mon._log_file = None

        content = (tmp_path / "activity.log").read_text()
        assert "[POSITION] hello" in content
        # Line is prefixed with a "YYYY-MM-DD HH:MM:SS | " timestamp
        assert content.split(" | ", 1)[1].strip() == "[POSITION] hello"

    def test_write_to_log_noop_when_not_opened(self, tmp_path):
        config = MonitorConfig(mqtt=MQTTConfig(host="test.broker.com"), topic="msh/test/#")
        config.log.path = str(tmp_path / "activity.log")
        mon = MeshtasticMonitor(config)

        mon._write_to_log("should not be written")  # log never opened

        assert not (tmp_path / "activity.log").exists()

    def test_rotation_triggers_past_max_size(self, tmp_path):
        config = MonitorConfig(mqtt=MQTTConfig(host="test.broker.com"), topic="msh/test/#")
        config.log.path = str(tmp_path / "activity.log")
        config.log.max_size_mb = 10
        mon = MeshtasticMonitor(config)
        mon._log_max_bytes = 50  # force rotation quickly for the test
        mon._open_log()

        mon._write_to_log("x" * 60)  # exceeds threshold after this write
        mon._write_to_log("second line")
        mon._log_file.close()
        mon._log_file = None

        assert (tmp_path / "activity.log.1").exists()
        assert (tmp_path / "activity.log").exists()
        assert "second line" in (tmp_path / "activity.log").read_text()

    def test_tail_log_returns_last_n_lines(self, tmp_path):
        path = tmp_path / "activity.log"
        path.write_text("\n".join(f"line {i}" for i in range(1, 101)) + "\n")

        lines = MeshtasticMonitor._tail_log(str(path), 5)

        assert lines == [f"line {i}" for i in range(96, 101)]

    def test_tail_log_empty_file(self, tmp_path):
        path = tmp_path / "activity.log"
        path.write_text("")

        assert MeshtasticMonitor._tail_log(str(path), 10) == []

    def test_replay_log_prints_history(self, tmp_path, capsys):
        config = MonitorConfig(mqtt=MQTTConfig(host="test.broker.com"), topic="msh/test/#")
        config.log.path = str(tmp_path / "activity.log")
        config.log.replay_lines = 2
        (tmp_path / "activity.log").write_text("2024-01-01 00:00:00 | first\n2024-01-01 00:00:01 | second\n")

        mon = MeshtasticMonitor(config)
        mon._replay_log()

        out = capsys.readouterr().out
        assert "first" in out
        assert "second" in out
        assert "Replaying last 2 lines" in out

    def test_replay_log_disabled_when_replay_lines_zero(self, tmp_path, capsys):
        config = MonitorConfig(mqtt=MQTTConfig(host="test.broker.com"), topic="msh/test/#")
        config.log.path = str(tmp_path / "activity.log")
        config.log.replay_lines = 0
        (tmp_path / "activity.log").write_text("2024-01-01 00:00:00 | first\n")

        mon = MeshtasticMonitor(config)
        mon._replay_log()

        assert "Replaying" not in capsys.readouterr().out
