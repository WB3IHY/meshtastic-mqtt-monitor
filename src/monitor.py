"""Main monitor application for Meshtastic MQTT Monitor."""

import logging
import signal
import sys
from typing import Optional

from src import __version__
from src.config import MonitorConfig
from src.decoder import MessageDecoder
from src.formatter import OutputFormatter
from src.mqtt_client import MQTTClient
from src.node_database import NodeDatabase  # --- ADDED ---

logger = logging.getLogger(__name__)


class MeshtasticMonitor:
    """
    Main monitor application that coordinates all components.

    Manages MQTT client, message decoder, and output formatter to provide
    a complete monitoring solution for Meshtastic MQTT traffic.
    """

    def __init__(self, config: MonitorConfig):
        """
        Initialize Meshtastic monitor.

        Args:
            config: Complete monitor configuration
        """
        self.config = config
        self.mqtt_client: Optional[MQTTClient] = None
        self.decoder: Optional[MessageDecoder] = None
        self.formatter: Optional[OutputFormatter] = None
        self.node_db: Optional[NodeDatabase] = None  # --- ADDED ---
        self._running = False

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def start(self) -> None:
        """
        Start the monitor application.

        Initializes all components, connects to MQTT broker, subscribes to topics,
        and begins monitoring messages.
        """
        self._running = True

        # Display startup information
        self._display_startup_info()

        try:
            # Initialize decoder
            logger.info("Initializing message decoder...")
            self.decoder = MessageDecoder(self.config.channel_keys)

            # --- ADDED: Initialize node database before formatter so names
            #            can be resolved during output ---
            db_config = getattr(self.config, "database", None)
            if db_config and getattr(db_config, "enabled", False):
                db_path = getattr(db_config, "path", "nodes.db")
                keep_position_history = getattr(db_config, "keep_position_history", True)
                keep_telemetry_history = getattr(db_config, "keep_telemetry_history", True)
                logger.info(f"Initializing node database at: {db_path}")
                self.node_db = NodeDatabase(
                    db_path=db_path,
                    keep_position_history=keep_position_history,
                    keep_telemetry_history=keep_telemetry_history,
                )
                print(f"Node database enabled: {db_path}")
            else:
                logger.info("Node database disabled (not configured or enabled: false)")
            # --- END ADDED ---

            # Initialize formatter (pass node_db so From/To labels show names)
            logger.info("Initializing output formatter...")
            self.formatter = OutputFormatter(
                self.config.colors,
                self.config.display_fields,
                self.config.keywords,
                self.config.hardware_models,
                self.node_db,  # --- ADDED ---
            )

            # Initialize MQTT client
            logger.info("Initializing MQTT client...")
            self.mqtt_client = MQTTClient(
                self.config.mqtt,
                self._on_message_received,
            )

            # Connect to MQTT broker
            if not self.mqtt_client.connect():
                logger.error("Failed to connect to MQTT broker")
                sys.exit(1)

            # Wait for connection to establish
            import time

            max_wait = 10  # seconds
            waited = 0
            while not self.mqtt_client.is_connected() and waited < max_wait:
                time.sleep(0.5)
                waited += 0.5

            if not self.mqtt_client.is_connected():
                logger.error("Connection timeout - could not connect to MQTT broker")
                sys.exit(1)

            # Subscribe to configured topic
            logger.info(f"Subscribing to topic: {self.config.topic}")
            if not self.mqtt_client.subscribe(self.config.topic):
                logger.error(f"Failed to subscribe to topic: {self.config.topic}")
                sys.exit(1)

            print("\n" + "=" * 80)
            print("Monitor is running. Press Ctrl+C to stop.")
            print("=" * 80 + "\n")

            # Keep the main thread alive
            while self._running:
                time.sleep(1)

        except Exception as e:
            logger.error(f"Error in monitor application: {e}", exc_info=True)
            self.stop()
            sys.exit(1)

    def stop(self) -> None:
        """
        Stop the monitor application gracefully.

        Disconnects from MQTT broker and cleans up resources.
        """
        if not self._running:
            return

        self._running = False

        print("\n" + "=" * 80)
        print("Shutting down monitor...")
        print("=" * 80)

        # Disconnect MQTT client
        if self.mqtt_client:
            logger.info("Disconnecting from MQTT broker...")
            self.mqtt_client.disconnect()

        # --- ADDED: Close node database ---
        if self.node_db:
            logger.info("Closing node database...")
            self.node_db.close()
        # --- END ADDED ---

        logger.info("Monitor stopped")
        print("Monitor stopped successfully.")

    def _on_message_received(self, topic: str, payload: bytes) -> None:
        """
        Callback for when a message is received from MQTT.

        Coordinates decoder and formatter to process and display the message.

        Args:
            topic: MQTT topic the message was received on
            payload: Raw message payload bytes
        """
        try:
            # Decode the message
            decoded_message = self.decoder.decode(topic, payload)

            # --- ADDED: Update node database from decoded message ---
            if self.node_db:
                self._update_node_database(decoded_message)
            # --- END ADDED ---

            # Hide decode errors if configured
            if self.config.hide_decode_errors and decoded_message.packet_type == "DECODE_ERROR":
                return  # Skip decode errors

            # Apply filters if configured
            if self.config.filter_type:
                # Filter by packet type
                if decoded_message.packet_type != self.config.filter_type:
                    return  # Skip this message

            # Format the message
            formatted_output = self.formatter.format_message(decoded_message)

            # Apply text filter if configured
            if self.config.filter_text:
                # Case-insensitive search in the formatted output
                if self.config.filter_text.lower() not in formatted_output.lower():
                    return  # Skip this message

            # Display the formatted message
            print(formatted_output)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

    # --- ADDED: Node database update helper ---
    def _update_node_database(self, decoded_message) -> None:
        """
        Extract node data from a decoded message and persist it to the database.

        Handles NODEINFO_APP, POSITION, and TELEMETRY_APP packet types explicitly,
        and touches last_heard for all other packet types so every node that
        transmits gets tracked.

        Args:
            decoded_message: A decoded message object from MessageDecoder
        """
        try:
            fields = decoded_message.fields or {}
            packet_type = decoded_message.packet_type

            # Resolve node_id: prefer explicit node_id field, fall back to from/from_id
            node_id = (
                fields.get("node_id")
                or fields.get("from")
                or getattr(decoded_message, "from_id", None)
                or getattr(decoded_message, "sender", None)
            )

            if not node_id:
                return  # Can't persist without a node identifier

            if packet_type == "NODEINFO_APP":
                self.node_db.upsert_node_info(node_id, fields)

            elif packet_type == "POSITION":
                self.node_db.upsert_position(node_id, fields)

            elif packet_type == "TELEMETRY_APP":
                self.node_db.upsert_telemetry(node_id, fields)

            else:
                # For all other packet types, at minimum record that this
                # node was heard and increment its packet count.
                self.node_db.touch_node(node_id)

        except Exception as e:
            logger.warning(f"Failed to update node database: {e}", exc_info=True)
    # --- END ADDED ---

    def _display_startup_info(self) -> None:
        """Display startup information including version and configuration."""
        print("\n" + "=" * 80)
        print(f"Meshtastic MQTT Monitor v{__version__}")
        print("=" * 80)
        print(f"MQTT Broker: {self.config.mqtt.host}:{self.config.mqtt.port}")
        print(f"Username: {self.config.mqtt.username or '(none)'}")
        print(f"TLS/SSL: {'Enabled' if self.config.mqtt.use_tls else 'Disabled'}")
        print(f"Topic: {self.config.topic}")

        if self.config.channels:
            print(f"Channels: {', '.join(self.config.channels)}")
        else:
            print("Channels: All")

        if self.config.channel_keys:
            print(f"Encryption keys configured for: {', '.join(self.config.channel_keys.keys())}")
        else:
            print("Encryption keys: None configured")

        if self.config.keywords:
            print(f"Keyword highlights: {len(self.config.keywords)} configured")

        # Display active filters
        if self.config.filter_type:
            print(f"Filter: Only showing {self.config.filter_type} messages")
        if self.config.filter_text:
            print(f"Filter: Only showing messages containing '{self.config.filter_text}'")

        # --- ADDED: Show database status in startup banner ---
        db_config = getattr(self.config, "database", None)
        if db_config and getattr(db_config, "enabled", False):
            db_path = getattr(db_config, "path", "nodes.db")
            print(f"Node Database: Enabled ({db_path})")
        else:
            print("Node Database: Disabled")
        # --- END ADDED ---

        print("=" * 80 + "\n")

    def _signal_handler(self, signum: int, frame) -> None:
        """
        Handle shutdown signals (SIGINT, SIGTERM).

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.stop()
        sys.exit(0)
