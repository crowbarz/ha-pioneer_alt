# ha-pioneer_alt
Customised Home Assistant media_player custom component for Pioneer AVRs.

Added support for the following features:
- Auto-detect and create entities for Zones 1, 2, 3 and HDZONE
- Use of HTTP API to read device status with failover back to telnet
- Optionally poll device less frequently if all zones are powered off

The VSX-930 HTTP and telnet APIs can be unstable after a while, it is
recommended to connect the AVR to a smart plug and hard power cycle the
receiver after an idle period via automation. The component will automatically
disable the zone entities if it can't poll the receiver after
DEFAULT_REFRESH_MAX_RETRIES, which is hard coded to 5.
