# Tesla EVTV BMS

This custom integration for Home Assistant listens to a UDP port and decodes Tesla EVTV BMS CAN data into multiple sensors.

## Features

- Pack name and port configuration via UI
- Real-time UDP listener
- Sensors for voltage, current, SoC, power, cell stats, charge/discharge info
- Lightweight and local

## Installation (HACS)

1. Go to HACS → Integrations → Custom Repositories
2. Add `https://github.com/wreuvers/tesla_evtv_bms` as type `Integration`
3. Click “Install”
4. Restart Home Assistant
5. Go to Settings → Devices & Services → `+` → Tesla EVTV BMS
