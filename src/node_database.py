"""Node database for tracking Meshtastic nodes observed via MQTT."""

import sqlite3
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class NodeDatabase:
    """
    SQLite-backed database that tracks every Meshtastic node heard over MQTT.

    The `nodes` table holds the latest known state for each node (upserted on
    every relevant packet).  Optional history tables record a full time-series
    of position and telemetry updates so you can replay a node's movement or
    battery trend over time.
    """

    def __init__(
        self,
        db_path: str = "nodes.db",
        keep_position_history: bool = True,
        keep_telemetry_history: bool = True,
    ):
        """
        Open (or create) the SQLite database and initialise the schema.

        Args:
            db_path: Filesystem path for the SQLite file.
            keep_position_history: When True, every POSITION packet is appended
                to the `position_history` table in addition to updating `nodes`.
            keep_telemetry_history: When True, every TELEMETRY_APP packet is
                appended to the `telemetry_history` table in addition to
                updating `nodes`.
        """
        self.db_path = db_path
        self.keep_position_history = keep_position_history
        self.keep_telemetry_history = keep_telemetry_history

        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Write-ahead logging gives better concurrency if you query the DB
        # with an external tool while the monitor is running.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        logger.info(
            f"Node database opened: {db_path} "
            f"(position_history={keep_position_history}, "
            f"telemetry_history={keep_telemetry_history})"
        )

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables if they do not already exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id                 TEXT PRIMARY KEY,
                -- NodeInfo fields
                long_name               TEXT,
                short_name              TEXT,
                hardware_model          TEXT,
                role                    TEXT,
                -- Latest position
                latitude                REAL,
                longitude               REAL,
                altitude                INTEGER,
                position_updated_at     INTEGER,
                -- Latest telemetry
                battery_level           INTEGER,
                voltage                 REAL,
                channel_utilization     REAL,
                air_util_tx             REAL,
                snr                     REAL,
                rssi                    INTEGER,
                -- Tracking metadata
                first_heard             INTEGER NOT NULL,
                last_heard              INTEGER NOT NULL,
                packet_count            INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS position_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id     TEXT    NOT NULL,
                latitude    REAL,
                longitude   REAL,
                altitude    INTEGER,
                timestamp   INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS telemetry_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id         TEXT    NOT NULL,
                battery_level   INTEGER,
                voltage         REAL,
                snr             REAL,
                rssi            INTEGER,
                timestamp       INTEGER NOT NULL
            );
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Public upsert methods
    # ------------------------------------------------------------------

    def upsert_node_info(self, node_id: str, data: dict) -> None:
        """
        Persist node identity information from a NODEINFO_APP packet.

        Args:
            node_id: Meshtastic node identifier (e.g. "!a1b2c3d4").
            data: Decoded field dict from the packet.
        """
        now = int(time.time())
        self.conn.execute(
            """
            INSERT INTO nodes (node_id, long_name, short_name, hardware_model, role,
                               first_heard, last_heard)
            VALUES (:node_id, :long_name, :short_name, :hardware_model, :role,
                    :now, :now)
            ON CONFLICT(node_id) DO UPDATE SET
                long_name      = COALESCE(:long_name,      long_name),
                short_name     = COALESCE(:short_name,     short_name),
                hardware_model = COALESCE(:hardware_model, hardware_model),
                role           = COALESCE(:role,           role),
                last_heard     = :now,
                packet_count   = packet_count + 1
            """,
            {
                "node_id":        node_id,
                "long_name":      data.get("long_name"),
                "short_name":     data.get("short_name"),
                "hardware_model": str(data.get("hardware_model", "") or ""),
                "role":           str(data.get("role", "") or ""),
                "now":            now,
            },
        )
        self.conn.commit()
        logger.debug(f"upsert_node_info: {node_id}")

    def upsert_position(self, node_id: str, data: dict) -> None:
        """
        Persist GPS position data from a POSITION packet.

        Args:
            node_id: Meshtastic node identifier.
            data: Decoded field dict from the packet.
        """
        now = int(time.time())
        lat = data.get("latitude")
        lon = data.get("longitude")
        alt = data.get("altitude")

        self.conn.execute(
            """
            INSERT INTO nodes (node_id, latitude, longitude, altitude,
                               position_updated_at, first_heard, last_heard)
            VALUES (:node_id, :lat, :lon, :alt, :now, :now, :now)
            ON CONFLICT(node_id) DO UPDATE SET
                latitude            = COALESCE(:lat, latitude),
                longitude           = COALESCE(:lon, longitude),
                altitude            = COALESCE(:alt, altitude),
                position_updated_at = :now,
                last_heard          = :now,
                packet_count        = packet_count + 1
            """,
            {"node_id": node_id, "lat": lat, "lon": lon, "alt": alt, "now": now},
        )

        if self.keep_position_history:
            self.conn.execute(
                """
                INSERT INTO position_history (node_id, latitude, longitude, altitude, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (node_id, lat, lon, alt, now),
            )

        self.conn.commit()
        logger.debug(f"upsert_position: {node_id} lat={lat} lon={lon}")

    def upsert_telemetry(self, node_id: str, data: dict) -> None:
        """
        Persist telemetry data from a TELEMETRY_APP packet.

        Args:
            node_id: Meshtastic node identifier.
            data: Decoded field dict from the packet.
        """
        now = int(time.time())
        batt    = data.get("battery_level")
        voltage = data.get("voltage")
        snr     = data.get("snr")
        rssi    = data.get("rssi")

        self.conn.execute(
            """
            INSERT INTO nodes (node_id, battery_level, voltage, snr, rssi,
                               first_heard, last_heard)
            VALUES (:node_id, :batt, :voltage, :snr, :rssi, :now, :now)
            ON CONFLICT(node_id) DO UPDATE SET
                battery_level = COALESCE(:batt,    battery_level),
                voltage       = COALESCE(:voltage,  voltage),
                snr           = COALESCE(:snr,      snr),
                rssi          = COALESCE(:rssi,     rssi),
                last_heard    = :now,
                packet_count  = packet_count + 1
            """,
            {
                "node_id": node_id,
                "batt":    batt,
                "voltage": voltage,
                "snr":     snr,
                "rssi":    rssi,
                "now":     now,
            },
        )

        if self.keep_telemetry_history:
            self.conn.execute(
                """
                INSERT INTO telemetry_history
                    (node_id, battery_level, voltage, snr, rssi, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (node_id, batt, voltage, snr, rssi, now),
            )

        self.conn.commit()
        logger.debug(f"upsert_telemetry: {node_id} batt={batt} voltage={voltage}")

    def touch_node(self, node_id: str) -> None:
        """
        Record that a node was heard without updating any specific fields.

        Called for every packet type that isn't NODEINFO_APP, POSITION, or
        TELEMETRY_APP so that every transmitting node appears in the database
        even if we never decode its full identity.

        Args:
            node_id: Meshtastic node identifier.
        """
        now = int(time.time())
        self.conn.execute(
            """
            INSERT INTO nodes (node_id, first_heard, last_heard)
            VALUES (?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                last_heard   = ?,
                packet_count = packet_count + 1
            """,
            (node_id, now, now, now),
        )
        self.conn.commit()
        logger.debug(f"touch_node: {node_id}")

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_all_nodes(self):
        """Return all nodes ordered by most recently heard."""
        return self.conn.execute(
            "SELECT * FROM nodes ORDER BY last_heard DESC"
        ).fetchall()

    def get_node(self, node_id: str):
        """Return a single node row, or None if not found."""
        return self.conn.execute(
            "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()

    def get_position_history(self, node_id: str):
        """Return full position history for a node, oldest first."""
        return self.conn.execute(
            "SELECT * FROM position_history WHERE node_id = ? ORDER BY timestamp ASC",
            (node_id,),
        ).fetchall()

    def get_telemetry_history(self, node_id: str):
        """Return full telemetry history for a node, oldest first."""
        return self.conn.execute(
            "SELECT * FROM telemetry_history WHERE node_id = ? ORDER BY timestamp ASC",
            (node_id,),
        ).fetchall()

    def get_node_count(self) -> int:
        """Return total number of unique nodes in the database."""
        row = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Flush any pending writes and close the database connection."""
        try:
            self.conn.commit()
            self.conn.close()
            logger.info(f"Node database closed: {self.db_path}")
        except Exception as e:
            logger.warning(f"Error closing node database: {e}")
