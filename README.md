# Voltcraft SEM6000 / SPB012BLE Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

A Home Assistant custom component integration for **Voltcraft SEM6000** and **SPB012BLE** Bluetooth Low Energy (BLE) smart power plugs.

## Currently Supported

- Turn outlet on/off
- Monitor outlet state (on/off)
- Automatic Discovery
- Real-time sensor monitoring (updated every 5 seconds):
  - Power consumption (Watts)
  - Voltage (Volts)
  - Current (Amperes)
  - Frequency (Hz)
  - Power factor (0.0-1.0)
  - Total consumed energy (kWh)

## Missing Capabilities

The device supports additional features (commands), which I don't plan to support (but PRs are welcome!):
- Time sync (needed for scheduled power on/off)
- Schedule power on/off (use Home Assistant automations instead)
- Get/set device name
- Set the power limit (called Power Protection in the app)
- Power consumption history (Home Assistant will already collect power consumption data by itself)
- Password protection
- Calibration
- Firmware update

## Requirements

### Hardware

- A Bluetooth adapter for your Home Assistant
- Voltcraft SEM6000 or SPB012BLE smart power plug

### Software

- Home Assistant
- Bluetooth integration enabled
- HACS (Recommended)

## Installation

### HACS Installation (Recommended)

1. Open HACS in your Home Assistant instance
2. Click on **Integrations**
3. Click the three dots in the top right corner and select **Custom repositories**
4. Add this repository URL: `https://github.com/Anty0/homeassistant-voltcraft_sem6000_spb012ble-integration`
5. Select **Integration** as the category
6. Click **Add**
7. Find "Voltcraft SEM6000 / SPB012BLE" in the integration list and click **Download**
8. Restart Home Assistant

### Manual Installation

1. Download the latest release from this repository
2. Copy the `custom_components/voltcraft_sem6000_spb012ble` directory to your Home Assistant's `custom_components` directory:
   ```
   <config_directory>/custom_components/voltcraft_sem6000_spb012ble/
   ```
   If the `custom_components` directory doesn't exist, create it first.
3. Restart Home Assistant

## Configuration

### Device Discovery

The Home Assistant should automatically discover Voltcraft SEM6000 and SPB012BLE devices via Bluetooth. Make sure:
- Bluetooth integration is enabled
- The device is powered on
- The device is within Bluetooth range of your Home Assistant host

If the device is found, it will appear in the integration list under **Devices & Services**, where you can configure it.

### Setup via UI

1. Ensure your Voltcraft SEM6000 / SPB012BLE device is powered on and within Bluetooth range
2. In Home Assistant, go to **Settings** → **Devices & Services**
3. Click **+ Add Integration**
4. Search for "Voltcraft SEM6000 / SPB012BLE"
5. Select your device from the list of discovered Bluetooth devices
6. Confirm the device selection
7. The integration will create a switch entity for your power plug

## Entities

Once configured, the integration creates the following entities:

### Switch Entity

- **Entity ID**: `switch.[device_mac_address]`
- **Device Class**: Outlet
- **Attributes**:
  - `is_on`: Current state of the outlet (true/false)
- **Services**:
  - `switch.turn_on`: Turn the outlet on
  - `switch.turn_off`: Turn the outlet off
  - `switch.toggle`: Toggle the outlet state

### Sensor Entities

All sensor values are updated every 5 seconds:

- **Power** (`sensor.[device_mac_address]_power`)
  - Current power consumption in Watts (W)
  - Device Class: Power
  - State Class: Measurement

- **Voltage** (`sensor.[device_mac_address]_voltage`)
  - Line voltage in Volts (V)
  - Device Class: Voltage
  - State Class: Measurement

- **Current** (`sensor.[device_mac_address]_current`)
  - Current draw in Amperes (A)
  - Device Class: Current
  - State Class: Measurement

- **Frequency** (`sensor.[device_mac_address]_frequency`)
  - Line frequency in Hertz (Hz)
  - Device Class: Frequency
  - State Class: Measurement

- **Power Factor** (`sensor.[device_mac_address]_power_factor`)
  - Power factor (0.0-1.0, dimensionless)
  - Device Class: Power Factor
  - State Class: Measurement

- **Total Energy** (`sensor.[device_mac_address]_energy`)
  - Cumulative energy consumption in kilowatt-hours (kWh)
  - Device Class: Energy
  - State Class: Total Increasing

## Enable Debug Logging

To enable debug logging for troubleshooting, add the following to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.voltcraft_sem6000_spb012ble: debug
```

Then restart Home Assistant.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an issue for:
- Bug reports
- Feature requests
- Protocol improvements
- Code enhancements

## Credits

- Protocol reverse-engineered by monitoring the official Android app and ravaging through other public repositories
- Inspiration taken from [here](https://codeberg.org/ldb/spb012ble) and [here](https://gitlab.youmi-lausanne.ch/amasson/hass-voltcraft-sem6000)

## License

This project is licensed under the MIT License—see the LICENSE file for details.

## Disclaimer

This is an unofficial integration and is not affiliated with or endorsed by Voltcraft or the device manufacturers. Use at your own risk.
