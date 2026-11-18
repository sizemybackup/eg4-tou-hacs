# EG4 Local TOU Manager (Home Assistant, HACS)

Cycle your EG4 12000XP battery bank on a time-of-use tariff **entirely locally**:
no cloud account, no EG4 portal. It talks Modbus TCP through an RS485-to-Ethernet
gateway and keeps the inverter's overnight AC-charge window, AC First (hold)
windows, charge limits and AC charge power matched to the day's tariff profile.

Built and field-tested on an EG4 12000XP V2 with two 314Ah 16 kW wall-mount
batteries on a Maryland BGE Schedule RL tariff.

> **Read the safety section before enabling read-write mode.**

**New to this? Start with the [wiring and setup guide](https://sizemybackup.com/eg4-12000xp-home-assistant-local-guide.html)**: parts list, photos, the gateway settings that work, and how this integration fits in.

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

1. HACS, Custom repositories, add this repo as type *Integration*.
2. Install, restart Home Assistant.
3. Settings, Devices & services, Add integration, "EG4 Local TOU Manager".
4. Choose **Scan my network for the gateway** (or enter the address manually).
   The scan checks your local /24 for devices with Modbus port 502 open, then
   reads two registers to confirm one is an EG4. It only reads, and takes a few
   seconds. Then confirm port (502), unit (1) and your country/state (for holidays).
5. Pick your tariff: the BGE preset, **custom summer + winter**, or **one schedule
   all year**. For custom schedules you enter the summer dates and 1-2 weekday
   on-peak windows per season. The 12000XP's three AC First windows can fence off
   at most **two peaks per day**. Change it later from the integration's
   *Configure* button.
6. Choose **read-only** (default, nothing is ever written) or **read-write**
   (asks you to confirm).

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
uv run --no-project --with pytest-homeassistant-custom-component --with holidays python -I -m pytest
```

## License

MIT, see [LICENSE](LICENSE). This is community software, not affiliated with or endorsed by EG4 Electronics.
