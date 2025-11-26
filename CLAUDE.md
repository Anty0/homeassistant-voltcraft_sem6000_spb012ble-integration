# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom component integration for Voltcraft SEM6000 / SPB012BLE Bluetooth Low Energy (BLE) smart power plugs. The integration enables local control of these devices via Bluetooth, allowing users to turn the outlet on/off, monitor its state, and track real-time power measurements (power, voltage, current, frequency, power factor, and total energy consumption). The integration uses a DataUpdateCoordinator pattern to poll the device every 5 seconds for sensor updates.

## Repository Structure

```
custom_components/voltcraft_sem6000_spb012ble/
├── __init__.py          # Integration setup, creates coordinator and loads platforms
├── config_flow.py       # Configuration flow for device discovery
├── const.py             # Constants (domain, UUIDs, device names, polling interval)
├── coordinator.py       # DataUpdateCoordinator for BLE communication and polling
├── manifest.json        # Integration metadata and dependencies
├── protocol.py          # BLE protocol implementation (reverse-engineered)
├── sensor.py            # Sensor entity implementations (6 sensors)
├── strings.json         # UI strings for configuration
└── switch.py            # Switch entity implementation (uses coordinator)
```

## Architecture

### DataUpdateCoordinator Pattern

The integration uses Home Assistant's `DataUpdateCoordinator` pattern for centralized BLE communication:

1. **Coordinator** (`coordinator.py`): `VoltcraftDataUpdateCoordinator`
   - Owns the BLE connection (established via `bleak-retry-connector`)
   - Registers persistent notification handler on `NOTIFY_UUID`
   - Polls device every 5 seconds by sending MEASURE command
   - Processes ALL notifications (solicited and unsolicited) asynchronously
   - Distributes measurement data to all entities atomically

2. **Entities**: Switch and 6 sensor entities extend `CoordinatorEntity`
   - Subscribe to coordinator updates
   - Automatically marked unavailable if coordinator fails
   - Get state from `coordinator.data`

### BLE Communication Flow

1. **Discovery**: Bluetooth devices discovered via Home Assistant's bluetooth integration using service UUID `0000fff0-0000-1000-8000-00805f9b34fb`
2. **Connection**: BLE connection established in `__init__.py` using `bleak-retry-connector`
3. **Commands**: Sent via GATT characteristic `COMMAND_UUID` (0xfff3)
4. **Notifications**: Received via `NOTIFY_UUID` (0xfff4)
5. **Polling**: Coordinator sends MEASURE command every 5 seconds, device responds via notification

### Protocol Details (protocol.py)

The BLE protocol was reverse-engineered by monitoring Android app communication. Payload structure:
- Header: `0x0F`
- Length byte
- Command byte (SWITCH=0x03, MEASURE=0x04)
- Params
- Checksum (validation disabled - checksums from device appear incorrect)
- Footer: `0xFF 0xFF`

Commands are built using `Command.build_payload()` and responses parsed via `NotifyPayload.from_payload()`. Two notification types:
- `MeasureNotifyPayload`: Contains device state (is_on) and measurement data (power, voltage, current, frequency, power_factor, consumed_energy)
- `SwitchNotifyPayload`: Confirms switch state change

**Unit conversions** in `VoltcraftData.from_payload()`:
- Power: milliwatts → watts (÷1000)
- Current: milliamps → amps (÷1000)
- Power factor: 0-256 → 0.0-1.0 (÷256)
- Voltage, frequency, consumed_energy: no conversion

### Entity Implementations

**Switch Entity** (`switch.py`): `MainSwitchEntity` extends `CoordinatorEntity` and `SwitchEntity`
- Device class: OUTLET
- State from `coordinator.data.is_on` with optimistic updates (`_attr_is_on_next`)
- Commands sent via `coordinator.async_send_switch_command()`
- Triggers coordinator refresh after switch commands

**Sensor Entities** (`sensor.py`): Six sensor classes extend `CoordinatorEntity` and `SensorEntity`
- Power, Voltage, Current, Frequency, Power Factor, Total Energy
- Values from `coordinator.data` properties
- State classes: MEASUREMENT (5 sensors) and TOTAL_INCREASING (energy)

### Configuration Flow (config_flow.py)

Two discovery paths:
1. **Bluetooth auto-discovery**: `async_step_bluetooth()` - triggered when Home Assistant detects device
2. **Manual setup**: `async_step_user()` - user selects from list of discovered devices

Both paths lead to `async_step_confirm()` which creates the config entry with MAC address.

## Development Setup

This integration is installed as a Home Assistant custom component. To develop:

1. Activate virtual environment: `source .venv/bin/activate`
2. Install Home Assistant: Already included in `.venv` with required dependencies
3. Copy `custom_components/voltcraft_sem6000_spb012ble/` to your Home Assistant's `config/custom_components/` directory
4. Restart Home Assistant to load the integration

## Code Style

Follow `.editorconfig` settings:
- Indent: 4 spaces
- Line length: 120 characters
- Python-specific formatting rules defined for IntelliJ/PyCharm

## Development Workflow

### Installing Development Dependencies

```bash
pip install -r requirements-dev.txt
```

This installs:
- **ruff**: Fast Python linter and formatter
- **mypy**: Static type checker
- **homeassistant** and **bleak**: Runtime dependencies for type checking

### Running Quality Checks Locally

Before committing code, run these checks:

```bash
# Format code with ruff
ruff format .

# Check formatting (without making changes)
ruff format --check .

# Lint code
ruff check .

# Auto-fix linting issues where possible
ruff check --fix .

# Type check the integration
mypy custom_components/voltcraft_sem6000_spb012ble
```

### Configuration

Code quality tools are configured in `pyproject.toml`:
- **Ruff**: 120 character line length, Python 3.13 target
- **Mypy**: Strict type checking enabled with overrides for modules without type stubs (bleak, bleak_retry_connector, voluptuous)

### Continuous Integration

GitHub Actions automatically runs format checking, linting, and type checking on all pushes and pull requests. See `.github/workflows/lint.yml` for the CI configuration.

All three checks must pass before code can be merged.

## Key Implementation Notes

### BLE Connection Management
- Connection established in `__init__.py:async_setup_entry()` using `establish_connection()`
- Connection owned by coordinator, not individual entities
- Coordinator lifecycle:
  - `async_setup()`: Start notifications on NOTIFY_UUID
  - `async_shutdown()`: Stop notifications and disconnect client
  - Called from `__init__.py` setup/unload

### State Synchronization and Polling
- Device polled every 5 seconds via coordinator's `_async_update_data()`
- Coordinator sends MEASURE command asynchronously
- Notification handler processes responses and updates all entities via `async_set_updated_data()`
- Handles both solicited (from polling) and unsolicited notifications (manual switch press)

### Notification Handling
- Single persistent notification handler in coordinator
- Processes `MeasureNotifyPayload` → updates all entities
- Processes `SwitchNotifyPayload` → triggers immediate measure request
- Unknown payloads logged as warnings with hex dump

### Error Handling
- Coordinator raises `UpdateFailed` if no data available, marking all entities unavailable
- BLE errors caught and logged
- Checksum validation disabled in protocol.py (checksums from device appear incorrect)

## Extending the Integration

### Protocol Expansion
Additional commands may exist (not reverse-engineered). To discover:
- Monitor BLE traffic with nRF Connect or similar tools
- Document payload patterns in `protocol.py`
- Add new `Command` enum values and parsing logic
