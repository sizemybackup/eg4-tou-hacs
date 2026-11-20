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

## What you can do with it

* **Cycle the battery for cell life:** charge overnight to a modest ceiling (80/70/50%) instead of
  100%, and spend it only during the expensive hours.
* **Follow a time-of-use tariff without the cloud:** the hold windows move with the season, weekends,
  holidays and (for BGE) the spring/fall one-hour peak shift, with no EG4 account or portal involved.
* **See before you trust:** read-only mode shows exactly what it would change, register by register.
* **Adjust from Home Assistant:** change charge limits, charge power or the charge window from the
  dashboard or from your own automations (examples below), and the inverter follows within seconds.

## Dashboard

The setup wizard's last step creates a ready-made **EG4 TOU** dashboard for you (tick
*Create the EG4 TOU dashboard*, on by default). It appears in the sidebar at `/eg4-tou`. An existing dashboard
at that address is never touched, and the dashboard is removed again when you remove the integration. The same
file is [`custom_components/eg4_tou/dashboard.yaml`](custom_components/eg4_tou/dashboard.yaml): status tiles
(mode, today's profile, settings out of sync), a **TOU & AC First Rules** status table (season and day type, billing window, next window, power
source, grid charging, next charge and inverter schedule, all with live countdowns; add your battery
sensors to the card to get Battery and Peak Reserve rows too), a **24-hour timeline** of today's on-peak hours,
hold windows and overnight charge window (with a now marker, and the inverter's own windows
drawn underneath if they differ from the plan), a **Today's rules** table showing for each
rule the plan, what the inverter holds now and whether they match, the list of pending
changes, and all the controls.

Times on both cards follow your Home Assistant profile (Profile, *Time format*: 12-hour or 24-hour). Set
`time_format: 12` or `24` on a card to override that for one card.

**Editing the schedule on the timeline card (administrators).** Press **Edit** on the timeline. Drag the handles
of the **on-peak windows** and of the overnight charge window (or a bar, to move the whole window; steps of 15 minutes,
or use the arrow keys on a focused handle). Windows cannot cross each other; **+ Add window** adds a second on-peak
window (the inverter supports two) and **Remove** deletes one. The **hold windows** (AC First) update live as you drag,
because they are simply the rest of the day. Open **Charge limits** and **AC charge power** for the sliders, and press
**Save**. Everything you changed is applied in one step (the `eg4_tou.update_settings` action), so the inverter never
sees half a schedule. **Cancel** throws the edit away, and **Reset to tariff** puts the tariff's own peak hours back.
The card warns if the charge window overlaps an on-peak period.

### Tariffs: an example, and your own named ones

The integration ships with one tariff, **BGE Schedule RL (example)**, which is read-only. Everyone else
creates their own, and you can keep several and switch between them (a summer plan, a second utility, a
trial):

* **Configure** (the button on the integration page): define a tariff (seasons and on-peak windows) and **name** it.
  It joins your list and becomes the active one. Using the name of a tariff you already made replaces it.
* **Edit on the timeline card**: change the on-peak windows of the active tariff and Save. On the built-in
  example the card asks you to **name your hours** instead, and saves them as a new tariff (the example stays
  as it was). **Save as new tariff…** copies the active tariff under a new name at any time.
* **Switch** with the *Tariff in use* selector (`select.eg4_tou_tariff`, also on the dashboard and usable in
  automations) or the Tariff picker in the card's Edit mode. **Delete** removes a tariff you made.

On-peak edits apply to the current **season** (every weekday of it; weekends and holidays stay all off-peak).
A tariff made from the example keeps its holiday list and daylight-saving shift (during the shift weeks the
card tells you the hours move an hour later). The charge window, charge limits and AC power are settings of the
integration and are the same for every tariff. In read-only mode a save changes the plan only; nothing is
written to the inverter.

To add it by hand instead (you unticked the box, or Home Assistant could not create it and told you so): Settings,
Dashboards, Add dashboard, *New dashboard from scratch*; open it, three dots, Edit dashboard, three dots,
**Raw configuration editor**, paste the file.
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

* Home Assistant 2025.1+, HACS.
* An RS485-to-Ethernet gateway set to Modbus TCP, wired to the inverter's RS485 port (see the
  [wiring and gateway guide](https://sizemybackup.com/eg4-12000xp-home-assistant-local-guide.html)).
* This is independent of, and does not replace, the EG4 monitoring integration.
  Run it alongside whichever monitoring integration you use.

### Supported devices

| Device | Status |
|---|---|
| EG4 12000XP (V2) | Supported: developed and run daily on one (firmware `ceaa-0508`). |
| Other EG4 models with the same register map (AC charge windows 68-73, AC First windows 152-157, AC charge SOC 160/161, AC charge power 66) | Untested. Use read-only mode first and compare "Today's rules" with the inverter's own screen. |
| EG4 inverters with a different register map, and non-EG4 inverters | Not supported. Setup's read-only probe may succeed on any Modbus device, so do not enable read-write on them. |
| Gateways | Any RS485-to-Modbus-TCP gateway that forwards Modbus RTU should work; the integration copes with gateways that broadcast replies to every client. |

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

**Later changes:** new gateway address, port, unit id or holiday country/state: integration's
three-dot menu, **Reconfigure** (tariff, mode and charge settings are kept; the new gateway is
probed read-only first). Tariff: the *Configure* button.

**Reporting a problem:** on the integration's page, three dots, **Download diagnostics**, and attach
the file to the issue. It contains your settings, the planned and current register values and the
week's schedule; the gateway address is removed.

A step-by-step walkthrough with screenshots: [Setting up the EG4 Local TOU Manager](https://sizemybackup.com/eg4-local-tou-manager-home-assistant.html).

### Setup fields

| Field | What to enter |
|---|---|
| Gateway | Local IP address or host name of the RS485-to-Ethernet gateway. Pre-filled when the scan finds one. Public internet addresses are refused. |
| Modbus TCP port | The gateway's Modbus TCP port, usually 502. |
| Modbus unit id | The inverter's Modbus address, 1 on a standard 12000XP. |
| Country code | Two-letter code (US, CA, ...) for public holidays in custom schedules. Checked against the holidays library. |
| State/region | Optional state or region code (MD, ...) for regional holidays. |
| Tariff | The BGE Schedule RL preset, custom summer + winter, or one schedule all year. |
| Weekends off-peak | Treat Saturdays and Sundays as all off-peak days. |
| Holidays off-peak | Custom schedules: treat public holidays as off-peak days. |
| Summer dates | Custom summer + winter: first and last day of summer; the rest of the year is winter. |
| On-peak windows | Custom schedules: 1 or 2 weekday on-peak windows per season (same day, no overlap). |
| Mode | Read-only (never writes) or read-write (asks you to confirm). |

### Settings you can change later

* **Configure** button: add a named tariff (the example, or custom seasons and peak windows, weekends, holidays) and switch to it.
* **Reconfigure** (three-dot menu): gateway address, port, unit id, holiday country/state.
* Entities on the device page (also on the dashboard):

| Entity | What it does |
|---|---|
| `switch.eg4_tou_controller_enabled` | Master switch. Off: nothing is planned or written. |
| `switch.eg4_tou_read_only_mode` | On: plan only, never write. Off: write differences to the inverter. |
| `select.eg4_tou_tariff` | Which saved tariff is in use. |
| `number.eg4_tou_charge_limit_summer_weekday` | AC charge end SOC on summer weekdays (20-100%). |
| `number.eg4_tou_charge_limit_winter_weekday` | AC charge end SOC on winter weekdays (20-100%). |
| `number.eg4_tou_charge_limit_weekend_holiday` | AC charge end SOC on weekends and holidays (20-100%). |
| `number.eg4_tou_ac_charge_power` | AC charge power while grid charging (1-10 kW). |
| `time.eg4_tou_charge_start`, `time.eg4_tou_charge_end` | The overnight AC charge window. |
| actions `eg4_tou.set_tariff`, `save_tariff_as`, `delete_tariff` | Switch to a saved tariff by name, copy the active one (optionally with new on-peak windows) under a new name, delete one you made. Administrators only. |
| action `eg4_tou.update_settings` | Change the on-peak windows, the charge window, the three charge limits and the AC charge power in one call (what the timeline card's Save uses). Administrators only; the window must end after it starts. |
| `sensor.eg4_tou_active_profile` | Today's profile: summer weekday, winter weekday or weekend / holiday. Attributes hold the plan, the timeline and the week ahead for the cards. |
| `sensor.eg4_tou_controller_status` | Off, read-only or live (writing); `last_write` attribute shows the last write and any error. |
| `sensor.eg4_tou_registers_out_of_sync` | How many inverter settings differ from the plan; `changes` lists them. |

## How data is updated

Every 10 minutes the integration opens a short Modbus connection, reads the managed registers (three
small blocks), works out today's plan and compares. In live mode it writes only the registers that
differ, in a safe order, then reads them back to confirm. Changing any setting re-plans straight away.
If the inverter cannot be read, the entities become unavailable and Home Assistant logs it once (and
once more when it recovers); the next cycle tries again. Nothing is kept in the cloud.

## Automation examples

Get a notification if the inverter's settings stay different from the plan in live mode:

```yaml
alias: EG4 TOU - settings not applied
triggers:
  - trigger: numeric_state
    entity_id: sensor.eg4_tou_registers_out_of_sync
    above: 0
    for: "00:30:00"
conditions:
  - condition: state
    entity_id: sensor.eg4_tou_controller_status
    state: live
actions:
  - action: persistent_notification.create
    data:
      title: EG4 TOU
      message: "Not applied: {{ state_attr('sensor.eg4_tou_registers_out_of_sync', 'changes') | join(', ') }}"
```

Charge to 100% while a storm helper is on, and back to 70% afterwards:

```yaml
alias: EG4 TOU - storm mode
triggers:
  - trigger: state
    entity_id: input_boolean.storm_mode
actions:
  - action: number.set_value
    target:
      entity_id: number.eg4_tou_charge_limit_winter_weekday
    data:
      value: "{{ 100 if trigger.to_state.state == 'on' else 70 }}"
```

Warn when the gateway has been unreachable for an hour:

```yaml
alias: EG4 TOU - gateway unreachable
triggers:
  - trigger: state
    entity_id: sensor.eg4_tou_controller_status
    to: unavailable
    for: "01:00:00"
actions:
  - action: persistent_notification.create
    data:
      title: EG4 TOU
      message: The Modbus gateway has not answered for an hour.
```

## Troubleshooting

* **Setup says "Could not read the inverter through that gateway":** check that the gateway is powered
  and on the same network, that it is in Modbus TCP mode on the port you entered, and the RS485 wiring
  and serial settings in the [wiring and gateway guide](https://sizemybackup.com/eg4-12000xp-home-assistant-local-guide.html).
* **All entities are unavailable:** the inverter could not be read in the last cycle. Check the gateway
  as above; the log has the reason. The integration retries every 10 minutes.
* **"Registers out of sync" stays above 0 in live mode:** something else is writing the same settings
  (an automation, the EG4 app or the inverter's screen), or the inverter refused a write. The
  `last_write` attribute of Controller status shows the error, and `changes` on the out-of-sync sensor
  lists what differs.
* **Another integration's EG4 sensors glitch now and then:** some gateways send every reply to every
  connected client. This integration keeps its connections short to limit that; see Known limitations.
* **A holiday is treated as a workday, or the other way round:** the BGE preset uses BGE's own holiday
  list. For custom schedules, check the country and state under Reconfigure, and the holiday checkbox
  under Configure.
* **Still stuck:** open an issue with the file from **Download diagnostics** (gateway address removed).

## Removing the integration

1. Settings, Devices & services, **EG4 Local TOU Manager**, three dots, **Delete**.
2. Remove it from HACS / the Marketplace, delete the dashboard if you added it by hand (the one the wizard created goes with the integration), and restart Home Assistant.

Removing it does **not** change the inverter: the last charge windows, hold windows and limits it wrote
stay in place. Set them back on the inverter or in the EG4 app if you want different ones.

## Security

Modbus TCP has no authentication or encryption: keep the gateway on a trusted network, never forward
port 502, and set a password on the gateway's web page. The integration is local-only, stores no
credentials, defaults to read-only, can write only a short whitelist of inverter settings, and its
network scan only reads and only touches your own private network. Details, and how to report a
problem privately, are in [SECURITY.md](SECURITY.md).

## Known limitations

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

## Keeping the screenshots and articles in step

The setup walkthrough and guides on sizemybackup.com show this integration's screens. So that they do not
drift, `tools/check_ui_docs.py` knows which source files change what users see
(`docs/ui-docs-map.json`) and records a hash of each group when the articles were last confirmed to match
(`docs/ui-docs-verified.json`).

- `python3 tools/check_ui_docs.py` lists every group and whether its articles and screenshots may be stale.
- Enable the commit check once per clone: `git config core.hooksPath .githooks`. It warns when a commit
  touches UI files that the articles show (`UI_DOCS_STRICT=1` makes it block instead).
- After retaking the screenshots and updating the articles: `python3 tools/check_ui_docs.py ack <group>`.
- Local paths to the articles are read from `~/.config/eg4-ui-docs.json`, which is not in the repo.

## Development

```
uv run --python 3.14 --no-project --with pytest-homeassistant-custom-component --with holidays \
    --with home-assistant-frontend --with pytest-cov python -I -m pytest --cov=custom_components/eg4_tou
uv run --python 3.14 --no-project --with homeassistant --with holidays --with mypy \
    --with types-python-dateutil python -I -m mypy --strict --follow-imports=silent custom_components/eg4_tou
node --test tests/frontend/*.test.mjs        # the cards' rendering
```

The CI (`.github/workflows/validate.yml`) runs all of these plus the HACS check and hassfest on every
push. `custom_components/eg4_tou/quality_scale.yaml` is a self-assessment against Home Assistant's
[integration quality scale](https://contributors.home-assistant.io/core/quality-scale/): every rule is
done or exempt with a reason. It is not an official rating; those exist only for integrations in core.
### Trying a development build

Tags with a suffix, like `v0.11.0-dev.1`, are published as GitHub **pre-releases**, which HACS hides unless you open
the repository in HACS, three dots, **Redownload**, and tick *Show beta versions*. To try a working tree without a
release, copy `custom_components/eg4_tou` into your Home Assistant's `config/custom_components/` and restart, or
run `uv run python tools/dev_instance.py` for a throwaway Home Assistant (read-only, login `dev` / `eg4-dev`) with it installed.

### Clean-install test

`tools/clean_install_test.py` downloads a **published** release from GitHub, installs it into a brand-new
Home Assistant container (host networking, so the wizard's scan can find a real gateway; read-only
mode), and checks the whole new-user journey: archive layout, setup wizard, entities, card files, the
cards rendering in a real browser, Configure and the reload it triggers, a Home Assistant restart, and a
clean uninstall. Run it before tagging a release:

```
uv run --with playwright --with pyyaml python tools/clean_install_test.py --ref v0.10.0
uv run --with playwright --with pyyaml python tools/clean_install_test.py --ref v0.10.0 \
    --ha-image ghcr.io/home-assistant/home-assistant:2025.1.4        # oldest supported version
```


## License

MIT, see [LICENSE](LICENSE). This is community software, not affiliated with or endorsed by EG4 Electronics.
