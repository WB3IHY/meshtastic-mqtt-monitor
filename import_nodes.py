#!/usr/bin/env python3
"""
Import node database from a live Meshtastic node into nodes.db.

Usage:
    python import_nodes.py --host 192.168.200.2
    python import_nodes.py --port /dev/ttyUSB0
    python import_nodes.py --host 192.168.200.2 --db /path/to/nodes.db
"""

import argparse
import sys
import time
import os

try:
    import meshtastic
    import meshtastic.serial_interface
    import meshtastic.tcp_interface
except ImportError:
    print("ERROR: meshtastic library not installed. Run: pip install meshtastic")
    sys.exit(1)

# Find the src directory relative to this script
script_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(script_dir, "src")
if os.path.isdir(src_path):
    sys.path.insert(0, src_path)
else:
    # Fallback: maybe we're already inside src/
    sys.path.insert(0, script_dir)

from node_database import NodeDatabase


def import_from_interface(iface, db: NodeDatabase) -> int:
    """Walk the interface's node list and upsert every entry into the DB."""
    nodes = iface.nodes
    if not nodes:
        print("No nodes found on device.")
        return 0

    imported = 0
    for node_id, node in nodes.items():
        # node_id is already a string like '!49b7a3c0'
        user            = node.get("user", {})
        position        = node.get("position", {})
        device_metrics  = node.get("deviceMetrics", {})
        last_heard      = node.get("lastHeard", int(time.time()))

        long_name  = user.get("longName", "")
        short_name = user.get("shortName", "")
        hw_model   = str(user.get("hwModel", ""))
        role       = str(user.get("role", ""))

        print(f"  {node_id:14s} | {long_name or '?'} ({short_name or '?'})")

        # Node info — always upsert so we at least get the name
        db.upsert_node_info(node_id, {
            "long_name":      long_name  or None,
            "short_name":     short_name or None,
            "hardware_model": hw_model   or None,
            "role":           role       or None,
        })

        # Position — the API returns pre-divided floats directly as
        # 'latitude' / 'longitude', and also raw integers as 'latitudeI' /
        # 'longitudeI'. Use whichever is available.
        lat = position.get("latitude")
        lon = position.get("longitude")
        if lat is None and "latitudeI" in position:
            lat = position["latitudeI"] / 1e7
        if lon is None and "longitudeI" in position:
            lon = position["longitudeI"] / 1e7
        alt = position.get("altitude")

        if lat is not None and lon is not None:
            db.upsert_position(node_id, {
                "latitude":  lat,
                "longitude": lon,
                "altitude":  alt,
            })

        # Telemetry
        if device_metrics:
            db.upsert_telemetry(node_id, {
                "battery_level": device_metrics.get("batteryLevel"),
                "voltage":       device_metrics.get("voltage"),
                "snr":           node.get("snr"),
            })

        # Preserve the node's own last_heard timestamp rather than using now()
        db.conn.execute(
            """UPDATE nodes SET
                   last_heard  = MAX(last_heard,  ?),
                   first_heard = MIN(first_heard, ?)
               WHERE node_id = ?""",
            (last_heard, last_heard, node_id),
        )
        db.conn.commit()
        imported += 1

    return imported


def main():
    parser = argparse.ArgumentParser(
        description="Import Meshtastic node DB into nodes.db"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="Serial port (e.g. /dev/ttyUSB0 or COM3)")
    source.add_argument("--host", help="Node hostname or IP for TCP connection")
    parser.add_argument(
        "--db", default="nodes.db",
        help="Path to SQLite DB (default: nodes.db in current directory)"
    )
    parser.add_argument(
        "--no-history", action="store_true",
        help="Skip writing to position/telemetry history tables"
    )
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    print(f"Opening node database: {db_path}")
    db = NodeDatabase(
        db_path=db_path,
        keep_position_history=not args.no_history,
        keep_telemetry_history=not args.no_history,
    )

    print("Connecting to Meshtastic node...")
    iface = None
    try:
        if args.port:
            iface = meshtastic.serial_interface.SerialInterface(args.port)
        else:
            iface = meshtastic.tcp_interface.TCPInterface(args.host)

        # Give the interface a moment to populate its node list
        print("Waiting for node list...")
        time.sleep(3)

        node_count = len(iface.nodes) if iface.nodes else 0
        print(f"Connected — {node_count} nodes in device database.")
        print()

        count = import_from_interface(iface, db)

        print()
        print(f"Done — imported {count} nodes into {db_path}")
        print(f"Total nodes now in DB: {db.get_node_count()}")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if iface:
            iface.close()
        db.close()


if __name__ == "__main__":
    main()
