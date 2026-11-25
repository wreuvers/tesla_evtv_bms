# Tesla EVTV BMS

This custom integration for Home Assistant listens to a UDP port and decodes Tesla EVTV BMS CAN data into multiple sensors.

## Features

- Pack name and port and detail configuration via UI
- Real-time UDP listener
- Sensors for voltage, current, SoC, power, cell stats, charge/discharge info
- Utility sensor for Charge and Discharge by Hour, Day, Week, Month, Year
- Trigger Cell to make everything safe :)
- Lightweight and local
- v1.3.7: read-only telnet poll (no keys sent) for per-pack Gigavac
  contactor commanded/reported state, welded warning, Tesla internal
  contactor status, and last-read timestamp. Defaults: Powerpack0
  192.168.6.95, Powerpack1 .96, Powerpack2 .97, port 23.
- v1.3.8: guarded EVTV Gigavac switches for Powerpack1 and Powerpack2
  (`switch.powerpackN_evtv_negative_contactor` / `_positive_contactor`).
  Open/close send only telnet key 1 or 2 after a main-screen read; the
  config menu, welded warning, and (for close) non-user/automation
  contexts are blocked. Opening requires pack |current| at or below
  `open_current_max` (default 2.0 A) or the Sonoff connect path off.
  Closing both uses negative, 10 s precharge, then positive; sync gates
  apply if that would put PP1 and PP2 on the bus together. Powerpack0
  stays read-only. Services: `tesla_evtv_bms.open_contactors` /
  `close_contactors`.

## Installation (HACS)

1. Go to HACS → Integrations → Custom Repositories
2. Add `https://github.com/wreuvers/tesla_evtv_bms` as type `Integration`
3. Click “Install”
4. Restart Home Assistant
5. Go to Settings → Devices & Services → `+` → Tesla EVTV BMS
