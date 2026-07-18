"""Unit tests for MeshtasticMonitor's node-database wiring."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.config import MonitorConfig, MQTTConfig
from src.decoder import DecodedMessage
from src.monitor import MeshtasticMonitor


def make_message(packet_type, from_node="!12345678", fields=None):
    """Build a minimal DecodedMessage for feeding into _update_node_database."""
    return DecodedMessage(
        packet_type=packet_type,
        channel="LongFast",
        from_node=from_node,
        to_node="broadcast",
        timestamp=datetime.now(),
        fields=fields or {},
    )


@pytest.fixture
def monitor():
    config = MonitorConfig(mqtt=MQTTConfig(host="test.broker.com"), topic="msh/test/#")
    mon = MeshtasticMonitor(config)
    mon.node_db = MagicMock()
    return mon


class TestNodeIdResolution:
    """Regression tests for node_id resolution in _update_node_database.

    Previously this fell back to decoded_message.from_id / .sender, neither of
    which exist on DecodedMessage, so POSITION and TELEMETRY_APP packets (which
    carry no fields["node_id"]/["from"]) were silently dropped and never
    reached the database.
    """

    def test_position_uses_from_node(self, monitor):
        """POSITION packets persist using decoded_message.from_node."""
        msg = make_message(
            "POSITION",
            from_node="!aaaaaaaa",
            fields={"latitude": 1.0, "longitude": 2.0, "altitude": 5},
        )

        monitor._update_node_database(msg)

        monitor.node_db.upsert_position.assert_called_once_with("!aaaaaaaa", msg.fields)

    def test_telemetry_uses_from_node(self, monitor):
        """TELEMETRY_APP packets persist using decoded_message.from_node."""
        msg = make_message("TELEMETRY_APP", from_node="!bbbbbbbb", fields={"battery_level": 80})

        monitor._update_node_database(msg)

        monitor.node_db.upsert_telemetry.assert_called_once_with("!bbbbbbbb", msg.fields)

    def test_other_packet_types_touch_node_using_from_node(self, monitor):
        """Packet types with no dedicated handler still touch the node."""
        msg = make_message("ROUTING_APP", from_node="!cccccccc")

        monitor._update_node_database(msg)

        monitor.node_db.touch_node.assert_called_once_with("!cccccccc")

    def test_nodeinfo_prefers_explicit_node_id_field(self, monitor):
        """NODEINFO_APP still uses fields['node_id'] rather than from_node."""
        msg = make_message(
            "NODEINFO_APP",
            from_node="!ffffffff",
            fields={"node_id": "!dddddddd", "long_name": "Test"},
        )

        monitor._update_node_database(msg)

        monitor.node_db.upsert_node_info.assert_called_once_with("!dddddddd", msg.fields)

    def test_unknown_from_node_is_not_persisted(self, monitor):
        """DECODE_ERROR-style messages with from_node='unknown' are skipped."""
        msg = make_message("DECODE_ERROR", from_node="unknown", fields={"error": "boom"})

        monitor._update_node_database(msg)

        monitor.node_db.touch_node.assert_not_called()
        monitor.node_db.upsert_position.assert_not_called()

    def test_missing_from_node_is_not_persisted(self, monitor):
        """A message with no resolvable identifier at all is skipped."""
        msg = make_message("STATUS", from_node="", fields={})

        monitor._update_node_database(msg)

        monitor.node_db.touch_node.assert_not_called()

    def test_exception_in_db_call_is_swallowed(self, monitor):
        """A failure writing to the database logs a warning but doesn't raise."""
        monitor.node_db.touch_node.side_effect = Exception("disk full")
        msg = make_message("ROUTING_APP", from_node="!cccccccc")

        monitor._update_node_database(msg)  # Should not raise
