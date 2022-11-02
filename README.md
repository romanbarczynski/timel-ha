# TIMEL furnace controller to Home Assistant integration

This module reads details from TIMEL servers and pushes data into mqtt broker
(with home assistant configuration topics as well).

Data pushed:

- total power used
- temperature outside (from additional sensor)
- temperature inside (from room sensor)

## Quick start

Create `config.yml` from example.

NOTE: you'll need to tcpdump traffic from your mobile app to timel servers
in order to get encoded `device_encoded` and `device_pin` - it differs
from data you enter into app.

Build and run it:

```
docker-compose up -d --build
```


## Config

You can control how HA UI will display number input:
```yaml
setting_mode: slider
```

Values: `slider` (default) or `box`