# Tesla EVTV BMS

This custom integration for Home Assistant listens to a UDP port and decodes Tesla EVTV BMS CAN data into multiple sensors.

## Features

- Pack name and port and detail configuration via UI
- Real-time UDP listener
- Sensors for voltage, current, SoC, power, cell stats, charge/discharge info
- Utility sensor for Charge and Discharge by Hour, Day, Week, Month, Year
- Trigger Cell to make everything safe :)
- Lightweight and local

## Installation (HACS)

1. Go to HACS → Integrations → Custom Repositories
2. Add `https://github.com/wreuvers/tesla_evtv_bms` as type `Integration`
3. Click “Install”
4. Restart Home Assistant
5. Go to Settings → Devices & Services → `+` → Tesla EVTV BMS
