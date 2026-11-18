# Security

## Reporting a problem
Please report security problems privately: on this repository's **Security** tab choose **Report a
vulnerability** (GitHub private vulnerability reporting). Do not open a public issue for a security
problem. If that option is not shown, open a public issue that says only "security contact request"
and no details, and we will arrange a private channel. This is a spare-time community project, so
there is no guaranteed response time.

Only the latest release is supported.

## What this integration does and does not do
- **Local only.** It talks Modbus TCP to the gateway you configure, on your own network. It makes no
  cloud calls, has no account, and stores no passwords or tokens. The only things it stores are the
  gateway address, port, unit id, your country/state and your tariff settings.
- **Read-only first.** Setup defaults to read-only mode; read-write needs an explicit confirmation.
- **Narrow writes.** When allowed to write it can change only: AC charge power, the three AC charge
  windows, the three AC First windows, and the AC charge start and end SOC. Each register has a
  fixed whitelist and value range checked in code before anything is sent (`modbus.py`).
- **The network scan only reads, and only locally.** It checks the /24 of each of your own private
  network adapters for port 502 and reads two registers. It refuses to probe any public address.
  Setup also rejects a public IP address as the gateway.
- **Dashboard cards.** The two custom cards are plain JavaScript served by the integration from a
  fixed list of two files. They only read entity states, and escape all text they put on the page.

## Know the limits of Modbus
Modbus TCP has **no authentication and no encryption**. Anyone who can reach the gateway's port can
read and change the inverter's settings, with or without this integration. So:
- Keep the gateway on a trusted network (ideally an IoT VLAN) and **never forward port 502** to the
  internet.
- Set a password on the gateway's own web page.
- Treat everyone who can control entities in your Home Assistant as able to switch read-only mode off.
  Do not expose the integration's switches to voice assistants. The two switches are created as
  configuration entities so Home Assistant does not expose them by default, but check your own
  settings (Settings, Voice assistants, Expose).
- Some gateways deliver every reply to every connected client. That is not a security boundary.

## Dependencies
The integration needs the `holidays` Python package (declared in `manifest.json`) and uses
`python-dateutil`, which `holidays` brings in. Tests run in CI-style isolation with
`pytest-homeassistant-custom-component`; nothing from the tests ships in the integration.
