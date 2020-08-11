"""
Support for Pioneer Network Receivers.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.pioneer/
"""

# pylint: disable=logging-fstring-interpolation,broad-except

from datetime import timedelta

import logging
import voluptuous as vol

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import (
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_SET,
    SUPPORT_VOLUME_STEP,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_TIMEOUT,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.exceptions import PlatformNotReady

from .pioneer_avr import PioneerAVR

_LOGGER = logging.getLogger(__name__)

SUPPORT_PIONEER = (
    SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_VOLUME_MUTE
    | SUPPORT_VOLUME_SET
    | SUPPORT_VOLUME_STEP
)

SCAN_INTERVAL = timedelta(seconds=60)
# SCAN_INTERVAL = timedelta(seconds=5)
# SCAN_INTERVAL = timedelta(seconds=1)

DEFAULT_NAME = "Pioneer AVR"
DEFAULT_PORT = 8102  # Some Pioneer AVRs use 23
DEFAULT_TIMEOUT = 2
DEFAULT_COMMAND_DELAY = 0.1

CONF_COMMAND_DELAY = "command_delay"
CONF_VOLUME_WORKAROUND = "volume_workaround"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.socket_timeout,
        vol.Optional(
            CONF_COMMAND_DELAY, default=DEFAULT_COMMAND_DELAY
        ): cv.socket_timeout,
        vol.Optional(CONF_VOLUME_WORKAROUND, default=False): cv.boolean,
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Pioneer platform."""
    _LOGGER.debug("setup_platform() called")
    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)
    timeout = config.get(CONF_TIMEOUT)
    command_delay = config.get(CONF_COMMAND_DELAY)
    volume_workaround = config.get(CONF_VOLUME_WORKAROUND)

    try:
        ## Open AVR connection
        pioneer = PioneerAVR(
            host,
            port,
            timeout,
            scan_interval=SCAN_INTERVAL.total_seconds(),
            command_delay=command_delay,
            volume_workaround=volume_workaround,
        )
    except Exception as e:  # pylint: disable=invalid-name
        _LOGGER.error(f"Could not open AVR connection: {e}")
        raise PlatformNotReady

    _LOGGER.info(f"Adding entities for zones {pioneer.zones}")
    entities = []
    for zone in pioneer.zones:
        name = config.get(CONF_NAME)
        if zone != "1":
            name += " HDZone" if zone == "Z" else f" Zone {zone}"
        entity = PioneerZone(pioneer, zone, name, config)
        if entity:
            _LOGGER.debug(f"Created entity {name} for zone {zone}")
            entities.append(entity)
        if zone == "1":
            ## Set update callback to update Main Zone entity
            pioneer.set_update_callback(entity.update_callback)
    if entities:
        add_entities(entities)
        try:
            pioneer.update()
        except Exception as e:  # pylint: disable=invalid-name
            _LOGGER.error(f"Could not perform initial update of AVR: {e}")
            raise PlatformNotReady


class PioneerZone(MediaPlayerEntity):
    """Representation of a Pioneer zone."""

    def __init__(self, pioneer, zone, name, config):
        """Initialize the Pioneer zone."""
        _LOGGER.debug(f"PioneerZone.__init({zone})")
        self._pioneer = pioneer
        self._zone = zone
        self._name = name
        pioneer.set_zone_callback(zone, self.schedule_update_ha_state)

    @property
    def name(self):
        """Return the name of the zone."""
        return self._name

    @property
    def state(self):
        """Return the state of the zone."""
        state = self._pioneer.power.get(self._zone)
        if state is None:
            return STATE_UNKNOWN
        return STATE_ON if state else STATE_OFF

    @property
    def available(self):
        """Returns whether the device is available."""
        return self._pioneer.available

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        volume = self._pioneer.volume.get(self._zone)
        max_volume = self._pioneer.max_volume.get(self._zone)
        return volume / max_volume if (volume and max_volume) else 0

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._pioneer.mute.get(self._zone, False)

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_PIONEER

    @property
    def source(self):
        """Return the current input source."""
        return self._pioneer.source.get(self._zone)

    @property
    def source_list(self):
        """List of available input sources."""
        return self._pioneer.get_source_list()

    @property
    def media_title(self):
        """Title of current playing media."""
        return self._pioneer.source.get(self._zone)

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        attrs = {}
        volume = self._pioneer.volume.get(self._zone)
        max_volume = self._pioneer.max_volume.get(self._zone)
        if volume is not None and max_volume is not None:
            if self._zone == "1":
                volume_db = volume / 2 - 80.5
            else:
                volume_db = volume - 81
            attrs = {
                "device_volume": volume,
                "device_max_volume": max_volume,
                "device_volume_db": volume_db,
            }
        return attrs

    @property
    def should_poll(self):
        """Polling required for main zone only."""
        return True if self._zone == "1" else False

    def turn_on(self):
        """Turn the media player on."""
        return self._pioneer.turn_on(self._zone)

    def turn_off(self):
        """Turn off media player."""
        return self._pioneer.turn_off(self._zone)

    def select_source(self, source):
        """Select input source."""
        return self._pioneer.select_source(source, self._zone)

    def volume_up(self):
        """Volume up media player."""
        return self._pioneer.volume_up(self._zone)

    def volume_down(self):
        """Volume down media player."""
        return self._pioneer.volume_down(self._zone)

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        max_volume = self._pioneer.max_volume.get(self._zone)
        return self._pioneer.set_volume_level(round(volume * max_volume), self._zone)

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        if mute:
            return self._pioneer.mute_on(self._zone)
        else:
            return self._pioneer.mute_off(self._zone)

    def update(self):
        """ Poll properties periodically. """
        return self._pioneer.update()

    def update_callback(self):
        """ Schedule full properties update of all zones. """
        if self._zone == "1":
            self.schedule_update_ha_state(force_refresh=True)
