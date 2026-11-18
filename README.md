# EG4 Local TOU Manager (Home Assistant, HACS)

Cycle your EG4 12000XP battery bank on a time-of-use tariff **entirely locally**:
no cloud account, no EG4 portal. It talks Modbus TCP through an RS485-to-Ethernet
gateway and keeps the inverter's overnight AC-charge window, AC First (hold)
windows, charge limits and AC charge power matched to the day's tariff profile.

Built and field-tested on an EG4 12000XP V2 with two 314Ah 16 kW wall-mount
batteries on a Maryland BGE Schedule RL tariff.

> **Read the safety section before enabling read-write mode.**

**New to this? Start with the [wiring and gateway guide](https://sizemybackup.com/eg4-12000xp-home-assistant-local-guide.html)**: parts list, photos, the gateway settings that work, and how this integration fits in.

## What it does

Every 10 minutes it reads the inverter, works out what today should look like
(summer weekday, winter weekday, weekend/holiday), and writes **only the
registers that differ**.

| Profile | AC charge window | Charge to | AC First (grid carries the house) |
|---|---|---|---|
| Summer weekday | 01:00-05:00 | 80% | everything outside the on-peak hours |
| Winter weekday | 01:00-05:00 | 70% | everything outside the on-peak hours |
| Weekend / holiday | 01:00-05:00 | 50% | all day |

The result is a battery cycled between its discharge floor and a modest ceiling
(good for cell life), spent only on the expensive hours. Peak hours come from the
tariff preset; AC First windows are derived automatically as the complement.

## Dashboard

A ready-made dashboard (core cards only, no custom JavaScript) is in
[`dashboards/eg4_tou_dashboard.yaml`](dashboards/eg4_tou_dashboard.yaml): status tiles
(mode, today's profile, settings out of sync), a **TOU & AC First Rules** status table (season and day type, billing window, next window, power
source, grid charging, next charge and inverter schedule, all with live countdowns; add your battery
sensors to the card to get Battery and Peak Reserve rows too), a **24-hour timeline** of today's on-peak hours,
hold windows and overnight charge window (with a now marker, and the inverter's own windows
drawn underneath if they differ from the plan), a **Today's rules** table showing for each
rule the plan, what the inverter holds now and whether they match, the list of pending
changes, and all the controls.

Install it: Settings, Dashboards, Add dashboard, *New dashboard from scratch*; open it,
three dots, Edit dashboard, three dots, **Raw configuration editor**, paste the file.
The rules tables find the integration's entities by themselves. Tiles and controls use
the default entity ids (`sensor.eg4_tou_*`, `switch.eg4_tou_*`, ...); if yours differ,
find/replace them in the raw editor.

## Safety (please read)

* **Setup asks read-only or read-write; read-only is the default.** In read-only mode nothing is ever written. Look at "Registers out of sync"
  and the "Active profile" attributes, and only then turn off the "Read-only mode"
  switch. Read-write needs an explicit confirmation in setup.
* Writes are limited to a fixed whitelist (AC charge power, charge windows, AC
  First windows, AC charge start/end SOC) with range checks. Nothing else can be
  written.
* **Do not run two controllers.** If you have your own automation writing the
  same registers, disable it first.
* Register behaviour was verified on one firmware. Read-only first; report your
  firmware version if something looks different.
* You are changing your inverter's settings. No warranty.

## Requirements

* EG4 12000XP (V2) with a Modbus TCP gateway on its RS485 port. Other EG4 models
  that use the same register map may work; not tested.
* Home Assistant 2025.1+, HACS.
* This is independent of, and does not replace, the EG4 monitoring integration.
  Run it alongside whichever monitoring integration you use.

## Install

**Home Assistant 2026.11 or newer (the Marketplace):**
1. Settings, **Marketplace**, **Add from link**, paste `https://github.com/sizemybackup/eg4-tou-hacs`
   and press Add. (The Marketplace asks you to read a first-run warning, and to connect a GitHub
   account, before it will add a repository from a link.)
2. Open **EG4 Local TOU Manager**, press **Download**, then restart Home Assistant.

**Older Home Assistant with HACS:** HACS, three dots, Custom repositories, add the same URL as type
*Integration*, download it and restart.

Then: Settings, Devices & services, **Add integration**, search for **EG4 Local TOU Manager**.

1. The first screen scans your local /24 for the gateway automatically (devices with
   Modbus port 502 open, then two register reads to confirm an EG4; read-only, nothing
   is written). Any gateway found is pre-selected in the **Gateway** field. Accept it, pick
   another, or type your own address in the same field, and adjust port (502), unit (1) and
   your country/state (for holidays) on the same screen. Tick "Scan the network again" to
   rescan.
2. Pick your tariff: the BGE preset, **custom summer + winter**, or **one schedule
   all year**. For custom schedules you enter the summer dates and 1-2 weekday
   on-peak windows per season. The 12000XP's three AC First windows can fence off
   at most **two peaks per day**. Change it later from the integration's
   *Configure* button.
   Holidays: the BGE preset uses the holiday list printed in BGE's tariff (which is shorter
   than the public calendar: MLK Day, Veterans Day and the day after Thanksgiving are normal
   peak days). For custom schedules public holidays come from the country and state you
   enter, and a checkbox lets you ignore them if your utility bills those days as normal.
3. Choose **read-only** (default, nothing is ever written) or **read-write**
   (asks you to confirm).

A step-by-step walkthrough with screenshots: [Setting up the EG4 Local TOU Manager](https://sizemybackup.com/eg4-local-tou-manager-home-assistant.html).

## Security

Modbus TCP has no authentication or encryption: keep the gateway on a trusted network, never forward
port 502, and set a password on the gateway's web page. The integration is local-only, stores no
credentials, defaults to read-only, can write only a short whitelist of inverter settings, and its
network scan only reads and only touches your own private network. Details, and how to report a
problem privately, are in [SECURITY.md](SECURITY.md).

## Known limits in v0.1

* The scan covers one /24 per network adapter and only port 502. Gateways on
  another subnet or VLAN, or on a non-standard port, need manual entry.

* Custom tariffs: one or two seasons, 1-2 weekday on-peak windows each, weekends
  and holidays optionally all off-peak. Peaks cannot cross midnight (split them
  into two windows), and per-weekday or shoulder-rate tariffs are not modelled.
  The one-hour spring/fall peak shift exists only in the BGE preset.
* The BGE spring/fall one-hour peak shift treats the end Sundays as exclusive.
  That is an assumption; confirm against your tariff sheet.
* Winter peak-2 "top-up" (learns evening usage and adds a daytime charge) is
  not ported yet.
* Summer profile and the shift logic are verified in unit tests and against the
  author's inverter's current (winter) state, not yet over a live summer.
* Several RS485 gateways broadcast every reply to every connected client, which
  can make *other* clients' sensors glitch. This integration connects briefly
  every 10 minutes to keep that to a minimum.

## Development

```
uv run --no-project --with pytest-homeassistant-custom-component --with holidays --with home-assistant-frontend python -I -m pytest
node --test tests/frontend/card.test.mjs      # the timeline card's rendering
```

## License

MIT, see [LICENSE](LICENSE). This is community software, not affiliated with or endorsed by EG4 Electronics.
