# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Version Format

This project uses [Semantic Versioning](https://semver.org/):
- **MAJOR** version for incompatible API changes
- **MINOR** version for new functionality in a backwards compatible manner
- **PATCH** version for backwards compatible bug fixes

## Change Categories

Changes are grouped into the following categories:
- **Added** - New features
- **Changed** - Changes in existing functionality
- **Deprecated** - Soon-to-be removed features
- **Removed** - Removed features
- **Fixed** - Bug fixes
- **Security** - Security vulnerability fixes

---

## [0.2.0] - 2026-01-20

### Fixed
- **Meshtastic Decryption**: Corrected nonce construction to use packet metadata (packet ID + from node ID) instead of extracting from encrypted payload, following official Meshtastic encryption format
- **Channel Extraction**: Fixed channel extraction for JSON messages by checking the JSON data for channel field before falling back to topic parsing
- **Key Padding**: Added proper key padding logic for 1-byte keys (AQ==) that expand to default Meshtastic key

### Changed
- Updated `_decrypt_payload()` method to accept packet_id and from_node_id parameters for proper nonce construction
- Enhanced channel extraction with debug logging to help troubleshoot topic parsing issues
- Improved integration tests to use correct encryption format with proper nonce construction

### Added
- Debug logging for channel extraction showing topic parsing details
- Support for extracting channel from JSON message data when not present in topic

## [0.1.0] - 2024-11-15

### Added
- Initial release of Meshtastic MQTT Monitor
- MQTT client wrapper with connection handling and reconnection logic
- Message decoder with support for encrypted and unencrypted messages
- Protobuf message decoding for common packet types (POSITION, TEXT_MESSAGE_APP, TELEMETRY_APP, NODEINFO_APP)
- Output formatter with ANSI color support and keyword highlighting
- YAML-based configuration management with default values
- Command-line argument support for all configuration options
- Comprehensive test suite with unit and integration tests
- Documentation including README, CONTRIBUTING, and DEVELOPER guides
- Example configuration file with detailed comments
- Support for multiple channels with channel-specific encryption keys
- Customizable display fields for each packet type
- Configurable color schemes for packet types and keyword highlights
- Graceful shutdown handling with SIGINT/SIGTERM support
- Version information display via --version flag and startup output

[0.2.0]: https://github.com/meshtastic/meshtastic-mqtt-monitor/releases/tag/v0.2.0
[0.1.0]: https://github.com/meshtastic/meshtastic-mqtt-monitor/releases/tag/v0.1.0
