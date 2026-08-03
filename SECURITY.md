# Security policy

## Supported versions

Security fixes are provided for the latest published prerelease or stable release
of this maintained fork. Older beta builds may be replaced rather than patched.

## Reporting a vulnerability

Do not publish device credentials, Home Assistant tokens, complete BLE captures,
private hostnames or exploit details in a public issue.

Use GitHub's private **Report a vulnerability** function for this repository when
it is available. If private vulnerability reporting is not available, open a
minimal public issue asking the maintainer for a private contact channel and do
not include technical exploit details.

A useful private report contains:

- affected integration and Home Assistant versions;
- device model, hardware version and firmware version;
- required attacker access, such as an authenticated Home Assistant account or
  physical BLE proximity;
- reproducible steps and the expected security boundary;
- a redacted log excerpt or minimal proof of concept;
- whether credentials, stored data, switching or device configuration are
  affected.

## Security model

The integration communicates locally over Bluetooth Low Energy. It does not add
an HTTP endpoint or cloud service. The device PIN contains four digits and must
not be treated as a high-entropy secret. Home Assistant account security,
backups, network exposure and physical BLE proximity remain relevant.

Credential changes and destructive reset actions require Home Assistant
administrator access. Other custom device actions require control permission for
the plug's outlet entity. Complete BLE notification frames are redacted from
normal integration logs.
