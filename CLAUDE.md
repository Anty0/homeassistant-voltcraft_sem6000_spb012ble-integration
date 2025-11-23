# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom component integration for Voltcraft SEM6000 / SPB012BLE Bluetooth Low Energy (BLE) smart power plugs. The integration enables local control of these devices via Bluetooth, allowing users to turn the outlet on/off and monitor its state.

## Repository Structure

```
custom_components/voltcraft_sem6000_spb012ble/
├── __init__.py          # Integration setup and platform loading
├── config_flow.py       # Configuration flow for device discovery
├── const.py             # Constants (domain, UUIDs, device names)
├── manifest.json        # Integration metadata and dependencies
├── protocol.py          # BLE protocol implementation (reverse-engineered)
├── strings.json         # UI strings for configuration
└── switch.py            # Switch entity implementation
```

## Architecture

### BLE Communication Flow

1. **Discovery**: Bluetooth devices are discovered via Home Assistant's bluetooth integration using the service UUID `0000fff0-0000-1000-8000-00805f9b34fb`
2. **Connection**: BLE connection established using `bleak-retry-connector`
3. **Commands**: Commands sent via GATT characteristic `COMMAND_UUID` (0xfff3)
4. **Notifications**: Device state updates received via `NOTIFY_UUID` (0xfff4)

### Protocol Details (protocol.py)

The BLE protocol was reverse-engineered by monitoring Android app communication. Payload structure:
- Header: `0x0F`
- Length byte
- Command byte (SWITCH=0x03, MEASURE=0x04)
- Params
- Checksum
- Footer: `0xFF 0xFF`

Commands are built using `Command.build_payload()` and responses parsed via `NotifyPayload.from_payload()`. Two notification types:
- `MeasureNotifyPayload`: Contains device state (is_on) and measurement data (power, voltage, etc. - commented out but available)
- `SwitchNotifyPayload`: Confirms switch state change

### Entity Implementation (switch.py)

`MainSwitchEntity` extends Home Assistant's `SwitchEntity`:
- **Device class**: OUTLET
- **IoT class**: local_push (no polling)
- **Setup**: Registers notification handler on NOTIFY_UUID
- **State management**: State tracked via `_attr_is_on`, updated through notification callbacks
- **Commands**: Turn on/off via `SwitchModes.ON/OFF.build_payload()`

Initial state request (`async_measure()`) sent when entity is added to Home Assistant.

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
- Connection established in `switch.py:async_setup_entry()` using `establish_connection()`
- Connection lifecycle managed by entity:
  - Notifications started in `async_setup()`
  - Cleanup in `async_will_remove_from_hass()`: stop notifications and disconnect client

### State Synchronization
- Device doesn't report state changes proactively on switch
- Integration sends `MEASURE` command to poll current state
- `SwitchNotifyPayload` handling includes fallback to request measure if state unknown

### Error Handling
- Unknown payloads logged as warnings with hex dump for debugging
- Checksum validation commented out in protocol.py (checksums from device appear incorrect)

## Extending the Integration

### Adding Sensor Entities
The protocol supports reading power metrics (commented out in `MeasureNotifyPayload`):
- power (3 bytes)
- voltage (1 byte)
- current (2 bytes)
- frequency (1 byte)
- power_factor (2 bytes)
- consumed_energy (4 bytes)

To add sensors:
1. Uncomment fields in `MeasureNotifyPayload.from_data()`
2. Add `Platform.SENSOR` to `PLATFORMS` in `__init__.py`
3. Create `sensor.py` implementing sensor entities
4. Update `manifest.json` if needed

### Protocol Expansion
Additional commands may exist (not reverse-engineered). To discover:
- Monitor BLE traffic with nRF Connect or similar tools
- Document payload patterns in `protocol.py`
- Add new `Command` enum values and parsing logic
