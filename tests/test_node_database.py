"""Unit tests for the SQLite node database."""

import pytest

from src.node_database import NodeDatabase


@pytest.fixture
def db(tmp_path):
    """Create a NodeDatabase backed by a temp file with history enabled."""
    database = NodeDatabase(db_path=str(tmp_path / "nodes.db"))
    yield database
    database.close()


@pytest.fixture
def db_no_history(tmp_path):
    """Create a NodeDatabase with position/telemetry history disabled."""
    database = NodeDatabase(
        db_path=str(tmp_path / "nodes.db"),
        keep_position_history=False,
        keep_telemetry_history=False,
    )
    yield database
    database.close()


class TestSchemaInitialization:
    """Test database creation and schema setup."""

    def test_creates_db_file(self, tmp_path):
        """Opening a NodeDatabase creates the SQLite file on disk."""
        path = tmp_path / "created.db"
        assert not path.exists()

        database = NodeDatabase(db_path=str(path))
        try:
            assert path.exists()
        finally:
            database.close()

    def test_starts_empty(self, db):
        """A freshly created database has no nodes."""
        assert db.get_node_count() == 0
        assert db.get_all_nodes() == []


class TestUpsertNodeInfo:
    """Test persisting NODEINFO_APP data."""

    def test_insert_new_node(self, db):
        """Node info for an unseen node creates a new row."""
        db.upsert_node_info(
            "!12345678",
            {"long_name": "Test Node", "short_name": "TN", "hardware_model": 43, "role": 0},
        )

        node = db.get_node("!12345678")
        assert node is not None
        assert node["long_name"] == "Test Node"
        assert node["short_name"] == "TN"
        assert node["hardware_model"] == "43"
        assert node["packet_count"] == 1
        assert node["first_heard"] == node["last_heard"]

    def test_update_existing_node(self, db):
        """A second NODEINFO_APP updates fields and bumps packet_count."""
        db.upsert_node_info("!12345678", {"long_name": "First", "short_name": "F1"})
        db.upsert_node_info("!12345678", {"long_name": "Second", "short_name": "F2"})

        node = db.get_node("!12345678")
        assert node["long_name"] == "Second"
        assert node["short_name"] == "F2"
        assert node["packet_count"] == 2
        assert db.get_node_count() == 1

    def test_partial_update_preserves_existing_fields(self, db):
        """Fields omitted from a later update keep their previous value (COALESCE)."""
        db.upsert_node_info(
            "!12345678", {"long_name": "Keep Me", "short_name": "KM", "hardware_model": 1}
        )
        db.upsert_node_info("!12345678", {"long_name": None, "short_name": None})

        node = db.get_node("!12345678")
        assert node["long_name"] == "Keep Me"
        assert node["short_name"] == "KM"


class TestUpsertPosition:
    """Test persisting POSITION data."""

    def test_updates_latest_position(self, db):
        """upsert_position stores lat/lon/alt on the nodes row."""
        db.upsert_position("!aaaaaaaa", {"latitude": 37.7749, "longitude": -122.4194, "altitude": 15})

        node = db.get_node("!aaaaaaaa")
        assert node["latitude"] == pytest.approx(37.7749)
        assert node["longitude"] == pytest.approx(-122.4194)
        assert node["altitude"] == 15
        assert node["position_updated_at"] is not None

    def test_records_position_history_by_default(self, db):
        """Successive positions are appended to position_history."""
        db.upsert_position("!aaaaaaaa", {"latitude": 1.0, "longitude": 2.0, "altitude": 10})
        db.upsert_position("!aaaaaaaa", {"latitude": 3.0, "longitude": 4.0, "altitude": 20})

        history = db.get_position_history("!aaaaaaaa")
        assert len(history) == 2
        assert history[0]["latitude"] == pytest.approx(1.0)
        assert history[1]["latitude"] == pytest.approx(3.0)

    def test_history_disabled(self, db_no_history):
        """When keep_position_history is False, no history rows are written."""
        db_no_history.upsert_position("!aaaaaaaa", {"latitude": 1.0, "longitude": 2.0, "altitude": 10})

        assert db_no_history.get_position_history("!aaaaaaaa") == []
        node = db_no_history.get_node("!aaaaaaaa")
        assert node["latitude"] == pytest.approx(1.0)


class TestUpsertTelemetry:
    """Test persisting TELEMETRY_APP data."""

    def test_updates_latest_telemetry(self, db):
        """upsert_telemetry stores battery/voltage/snr/rssi on the nodes row."""
        db.upsert_telemetry(
            "!bbbbbbbb", {"battery_level": 85, "voltage": 4.1, "snr": 5.5, "rssi": -90}
        )

        node = db.get_node("!bbbbbbbb")
        assert node["battery_level"] == 85
        assert node["voltage"] == pytest.approx(4.1)
        assert node["snr"] == pytest.approx(5.5)
        assert node["rssi"] == -90

    def test_records_telemetry_history_by_default(self, db):
        """Successive telemetry updates are appended to telemetry_history."""
        db.upsert_telemetry("!bbbbbbbb", {"battery_level": 90})
        db.upsert_telemetry("!bbbbbbbb", {"battery_level": 80})

        history = db.get_telemetry_history("!bbbbbbbb")
        assert len(history) == 2
        assert history[0]["battery_level"] == 90
        assert history[1]["battery_level"] == 80

    def test_history_disabled(self, db_no_history):
        """When keep_telemetry_history is False, no history rows are written."""
        db_no_history.upsert_telemetry("!bbbbbbbb", {"battery_level": 90})

        assert db_no_history.get_telemetry_history("!bbbbbbbb") == []


class TestTouchNode:
    """Test touch_node used for packet types without dedicated handling."""

    def test_creates_node_on_first_touch(self, db):
        """touch_node inserts a bare row for a previously unseen node."""
        db.touch_node("!cccccccc")

        node = db.get_node("!cccccccc")
        assert node is not None
        assert node["packet_count"] == 1
        assert node["long_name"] is None

    def test_increments_packet_count(self, db):
        """Repeated touches bump packet_count and last_heard without touching identity fields."""
        db.upsert_node_info("!cccccccc", {"long_name": "Keep", "short_name": "K"})
        db.touch_node("!cccccccc")
        db.touch_node("!cccccccc")

        node = db.get_node("!cccccccc")
        assert node["packet_count"] == 3
        assert node["long_name"] == "Keep"


class TestQueryHelpers:
    """Test read-side query helpers."""

    def test_get_node_missing_returns_none(self, db):
        """Looking up a node that was never seen returns None."""
        assert db.get_node("!deadbeef") is None

    def test_get_all_nodes_ordered_by_last_heard(self, db):
        """get_all_nodes returns rows most-recently-heard first."""
        db.touch_node("!11111111")
        db.touch_node("!22222222")
        db.upsert_node_info("!11111111", {"long_name": "Most Recent"})

        nodes = db.get_all_nodes()
        assert [n["node_id"] for n in nodes] == ["!11111111", "!22222222"]

    def test_get_node_count(self, db):
        """get_node_count reflects the number of distinct nodes seen."""
        db.touch_node("!11111111")
        db.touch_node("!22222222")
        db.touch_node("!11111111")  # same node again

        assert db.get_node_count() == 2


class TestLifecycle:
    """Test open/close behavior."""

    def test_close_is_safe_to_call(self, tmp_path):
        """close() flushes and closes the connection without raising."""
        database = NodeDatabase(db_path=str(tmp_path / "nodes.db"))
        database.touch_node("!12345678")
        database.close()  # Should not raise

    def test_data_persists_across_reopen(self, tmp_path):
        """Data written in one session is readable after reopening the same file."""
        path = str(tmp_path / "nodes.db")

        first = NodeDatabase(db_path=path)
        first.upsert_node_info("!12345678", {"long_name": "Persistent"})
        first.close()

        second = NodeDatabase(db_path=path)
        try:
            node = second.get_node("!12345678")
            assert node is not None
            assert node["long_name"] == "Persistent"
        finally:
            second.close()
