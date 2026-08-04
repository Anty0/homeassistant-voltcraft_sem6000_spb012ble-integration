# Voltcraft SEM6000 / SPB012BLE 2.0.0-beta.6

This test build adds PIN management and applies the security hardening identified
in the pre-release source review.

## Changes

- Repair the repository component constants so the uploaded `main` code and the
  PIN-enabled coordinator/config flow can load consistently.
- Ask for the four-digit device PIN during initial setup using a masked field.
- Do not return the stored PIN to the browser in integration options.
- Add administrator-only device PIN change and PIN reset actions with explicit confirmation.
- Persist an acknowledged PIN change in the Home Assistant config entry.
- Restrict credential changes and destructive resets to administrators.
- Require outlet control permission for timer, schedule, random-mode and refresh
  actions.
- Validate incoming SEM6000 checksums before parsing state or acknowledgements.
- Reject malformed random-mode times and skip corrupted schedule entries.
- Redact complete BLE notification frames from normal debug and warning logs.
- Stop overriding Home Assistant's Bluetooth dependency from the custom
  integration; CI is pinned to Home Assistant 2026.7.4 and its exact BLE
  dependency versions.
- Pin GitHub Actions to immutable commit SHAs and reduce workflow permissions to
  read-only repository contents.
- Add protocol security regression tests and a security reporting policy.

## Test status

Completed for this package:

- Python syntax compilation
- JSON and YAML parsing
- protocol, log-redaction and package-consistency regression tests
- line-length and archive-content checks

Still pending in the GitHub pull request or on real hardware:

- Ruff formatting and lint checks
- mypy type checking
- hassfest and HACS validation
- Home Assistant setup, permissions and reconnect behavior
- device PIN change and reset
- local Bluetooth adapter and Bluetooth proxy paths

Do not publish this build as a prerelease until the GitHub checks and the separate
Home Assistant/BLE test checklist pass.

Hardware validation pending.
