# Voltcraft SEM6000 / SPB012BLE Integration for Home Assistant


An extended, independently maintained Home Assistant custom integration for the
**Voltcraft SEM6000** and protocol-compatible **SPB012BLE** Bluetooth Low Energy
power plugs.

> [!IMPORTANT]
> This repository is an independently maintained fork of
> [Anty0/homeassistant-voltcraft_sem6000_spb012ble-integration](https://github.com/Anty0/homeassistant-voltcraft_sem6000_spb012ble-integration).
> It retains the original Git history, license and attribution, but releases and
> issue handling for this extended version are maintained here.

## Project status

The current release line is **2.0.0 beta**. It contains a substantially expanded
protocol implementation and should be treated as a prerelease until it has been
validated on more devices and Bluetooth environments.

Development and testing are primarily performed with a Voltcraft SEM6000.
SPB012BLE support is inherited from the upstream integration and is currently
best effort; reports from SPB012BLE users are welcome.

## Features

### Outlet control and live measurements

- Automatic Bluetooth discovery and UI-based setup
- Outlet on/off control and state reporting
- Power, voltage, current and frequency
- Calculated power factor
- Persistent total-energy reading from the device
- Automatic recovery after temporary disconnects or power loss

### Device settings

- Automatic device-time synchronization after an authenticated connection
- Device name
- Four-digit device PIN authentication
- Night mode / LED ring control
- Over-power limit from 1 to 4000 W
- Over-power protection enable/disable control
- Normal and reduced electricity tariffs
- Reduced-tariff start and end times

### Timers, schedules and random mode

- Countdown timer read, start and stop operations
- Read, add, edit and remove device schedules
- Random mode enable/disable control
- Random-mode start and end times
- Individual weekday switches for random mode

### History and diagnostics

- Total energy suitable for Home Assistant long-term statistics
- Device energy history for the last 24 hours, 30 days and 12 months
- Device serial number, vendor, firmware and hardware information
- BLE connection mode and negotiated ATT MTU
- Diagnostics for the app-compatible initialization sequence

Some history and diagnostic entities are disabled by default and can be enabled
from the Home Assistant entity registry when needed.

## Known limitations

- The integration is still a prerelease and has mainly been tested with one
  SEM6000 hardware environment.
- The tested SEM6000 settings response does not reliably report the current
  over-power-protection state. The integration therefore preserves the last
  command-confirmed state instead of forcing the switch back to off.
- Calibration and firmware updates are not implemented.
- The official Voltcraft app and Home Assistant should not control the plug at
  the same time because the device normally accepts only one active BLE client.

## Requirements

- Home Assistant with the Bluetooth integration enabled
- A local Bluetooth adapter or a supported Bluetooth proxy
- Voltcraft SEM6000 or compatible SPB012BLE power plug
- HACS for the recommended installation method

## Installation

### HACS custom repository

1. Open HACS in Home Assistant.
2. Add the following repository as a custom **Integration** repository:

   ```text
   https://github.com/citizenserious/homeassistant-voltcraft_sem6000_spb012ble-integration
   ```

3. Install **Voltcraft SEM6000 / SPB012BLE**.
4. Select the prerelease version when installing a beta release.
5. Restart Home Assistant.
6. Open **Settings -> Devices & services** and configure the discovered plug.

### Manual installation

1. Download the desired release archive.
2. Copy the directory below into the Home Assistant configuration directory:

   ```text
   custom_components/voltcraft_sem6000_spb012ble/
   ```

3. Restart Home Assistant.
4. Configure the integration through **Settings -> Devices & services**.

## Migrating from the upstream integration

This fork deliberately keeps the same Home Assistant integration domain:

```text
voltcraft_sem6000_spb012ble
```

The upstream integration and this fork therefore **cannot be installed in
parallel**.

Before migrating, create a Home Assistant backup. Then replace the upstream HACS
custom-repository entry with this repository, install the selected release and
restart Home Assistant. Do not remove the configured Home Assistant integration
unless normal replacement fails, because removing it can also remove registry
entries and entity customizations.

The migration path has not yet been validated across every upstream version.
Check entity IDs, automations and the Energy dashboard after the first restart.

## Configuration and PIN

The integration is configured through the Home Assistant UI. Devices using the
default or a custom four-digit PIN are authenticated during connection setup.
The PIN must contain exactly four decimal digits.

## Services and advanced functions

Timer and schedule operations are exposed through integration services. Their
current state is also available through the Timer and Schedules entities. Use
Home Assistant's **Developer tools -> Actions** view to inspect the available
fields and target the correct Voltcraft device.

## Debug logging

Add the following to `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: info
  logs:
    custom_components.voltcraft_sem6000_spb012ble: debug
```

Downloaded Home Assistant logs can contain unrelated integration data, hostnames,
usernames and device identifiers. Review and redact complete logs before posting
them publicly.

## Reporting issues

When reporting a problem, include:

- Integration version
- Home Assistant Core version
- Device model, hardware version and firmware version when available
- Bluetooth path used: local adapter or proxy
- Relevant debug log section with private data removed
- Exact action that triggered the problem

Please report issues in this repository, not in the upstream repository, when
they concern features or releases provided only by this fork.

## Upstream relationship and credits

This project is based on the work by **Jiri Kuchynka (Anty)** in the original
integration. The original Git history and MIT license are retained.

The SEM6000 protocol implementation also builds on publicly available reverse
engineering and additional Android Bluetooth HCI captures used during development
of this fork.

Useful prior protocol work:

- [amasson/hass-voltcraft-sem6000](https://gitlab.youmi-lausanne.ch/amasson/hass-voltcraft-sem6000)

## Contributing

Bug reports, protocol captures, device-compatibility reports and focused pull
requests are welcome. Changes should preserve existing entity unique IDs unless
a documented migration is provided.

## License

Licensed under the MIT License. See [LICENSE](LICENSE).

## Disclaimer

This is an unofficial community integration. It is not affiliated with or
endorsed by Voltcraft or the device manufacturer. Device-control and protection
functions should be tested with a non-critical load before relying on them.
