"""
Support for Pioneer Network Receivers.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.pioneer/
"""
from datetime import timedelta

import logging
import telnetlib
import requests
import voluptuous as vol

from functools import reduce
from homeassistant.components.media_player import (
    PLATFORM_SCHEMA, MediaPlayerDevice)
from homeassistant.components.media_player.const import (
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_SELECT_SOURCE,
    SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_SET, SUPPORT_VOLUME_STEP)
from homeassistant.const import (
    CONF_HOST, CONF_NAME, CONF_PORT, CONF_TIMEOUT, CONF_SCAN_INTERVAL,
    STATE_OFF, STATE_ON, STATE_UNKNOWN)
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

ZONE_JSON_MAP = {
    '1': 'MZ',
    '2': 'Z2',
    '3': 'Z3',
    'Z': 'HZ'
}

SUPPORT_PIONEER = SUPPORT_TURN_ON | SUPPORT_TURN_OFF | \
                  SUPPORT_SELECT_SOURCE | SUPPORT_VOLUME_MUTE | \
                  SUPPORT_VOLUME_SET | SUPPORT_VOLUME_STEP

MAX_VOLUME = 185
MAX_VOLUME_ZONEX = 81
MAX_SOURCE_NUMBERS = 60

#SCAN_INTERVAL = timedelta(seconds=5)
SCAN_INTERVAL = timedelta(seconds=1)

CONF_USE_HTTP = 'use_http'
CONF_SLOW_REFRESH = 'slow_refresh'

DEFAULT_NAME = 'Pioneer AVR'
DEFAULT_PORT = 23   # telnet default. Some Pioneer AVRs use 8102
DEFAULT_TIMEOUT = SCAN_INTERVAL.total_seconds()*3/4
DEFAULT_SLOW_REFRESH = 5 # Scan interval multipler when all devices off
DEFAULT_REFRESH_MAX_RETRIES = 3 # Number of retries before marking unavailable

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.socket_timeout,
    vol.Optional(CONF_SLOW_REFRESH, default=DEFAULT_SLOW_REFRESH): cv.positive_int,
    vol.Optional(CONF_USE_HTTP, default=True): cv.boolean
})


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Pioneer platform."""
    _LOGGER.debug("setup_platform() called")
    pioneer = PioneerDevice(
        config.get(CONF_NAME), config.get(CONF_HOST), config.get(CONF_PORT),
        config.get(CONF_TIMEOUT), config.get(CONF_SLOW_REFRESH),
        config.get(CONF_USE_HTTP))

    if pioneer.update():
        msg = "Init complete, adding entities: "
        for z in pioneer.zones:
            msg += "%s [%s](%x), " % (pioneer.zones[z], z, id(pioneer.zones[z]))
        _LOGGER.debug(msg)
        add_entities(pioneer.zones.values())

class PioneerZone(MediaPlayerDevice):
    """Representation of a Pioneer zone."""

    def __init__(self, name, zone, parent_device):
        """Initialize the Pioneer zone."""
        _LOGGER.debug("PioneerZone(%s) started", zone)
        self._name = name
        self._zone = zone
        self._parent_device = parent_device
        self._available = False
        self._pwstate = None
        self._max_volume = MAX_VOLUME if zone == "1" else MAX_VOLUME_ZONEX
        self._volume = 0
        self._muted = False
        self._source = ''

    @property
    def should_poll(self):
        """No polling needed for zones."""
        return False

    @property
    def name(self):
        """Return the name of the zone."""
        return self._name

    @property
    def state(self):
        """Return the state of the zone."""
        if self._pwstate == None:
            return STATE_UNKNOWN
        return STATE_ON if self._pwstate else STATE_OFF

    @property
    def available(self):
        """Returns whether the device is available."""
        return self._available

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self._volume / self._max_volume if self._volume else 0

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_PIONEER

    @property
    def source(self):
        """Return the current input source."""
        return self._source

    @property
    def source_list(self):
        """List of available input sources."""
        # _LOGGER.warning("Source list update %x call to %x", id(self), id(self._parent_device))
        return self._parent_device.get_source_list()
        
    @property
    def media_title(self):
        """Title of current playing media."""
        return self._source

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        return {
            'device_volume': self._volume,
            'device_max_volume': self._max_volume,
            'parent_entity': self._parent_device.entity_id,
            'update_source': self._parent_device._update_source
        }

    def turn_on(self):
        """Turn the media player on."""
        if self._zone == "1":
            self._parent_device.send_command("PO")
        elif self._zone == "2":
            self._parent_device.send_command("APO")
        elif self._zone == "3":
            self._parent_device.send_command("BPO")
        elif self._zone == "Z":
            self._parent_device.send_command("ZEO")
        else:
            _LOGGER.warning("Invalid service request for zone %s", self._zone)

    def turn_off(self):
        """Turn off media player."""
        if self._zone == "1":
            self._parent_device.send_command("PF")
        elif self._zone == "2":
            self._parent_device.send_command("APF")
        elif self._zone == "3":
            self._parent_device.send_command("BPF")
        elif self._zone == "Z":
            self._parent_device.send_command("ZEF")
        else:
            _LOGGER.warning("Invalid service request for zone %s", self._zone)

    def select_source(self, source):
        """Select input source."""
        source_number = self._parent_device.source_name_to_number(source)
        if source_number:
            if self._zone == "1":
                self._parent_device.send_command(source_number + "FN")
            elif self._zone == "2":
                self._parent_device.send_command(source_number + "ZS")
            elif self._zone == "3":
                self._parent_device.send_command(source_number + "ZT")
            elif self._zone == "Z":
                self._parent_device.send_command(source_number + "ZEA")
            else:
                _LOGGER.warning("Invalid service request for zone %s", self._zone)
        else:
            _LOGGER.warning("Invalid source %s for zone %s", source, self._zone)

    def volume_up(self):
        """Volume up media player."""
        if self._zone == "1":
            self._parent_device.send_command("VU")
        elif self._zone == "2":
            self._parent_device.send_command("ZU")
        elif self._zone == "3":
            self._parent_device.send_command("YU")
        elif self._zone == "Z":
            self._parent_device.send_command("HZU")
        else:
            _LOGGER.warning("Invalid service request for zone %s", self._zone)

    def volume_down(self):
        """Volume down media player."""
        if self._zone == "1":
            self._parent_device.send_command("VD")
        elif self._zone == "2":
            self._parent_device.send_command("ZD")
        elif self._zone == "3":
            self._parent_device.send_command("YD")
        elif self._zone == "Z":
            self._parent_device.send_command("HZD")
        else:
            _LOGGER.warning("Invalid service request for zone %s", self._zone)

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        # 60dB max
        if self._zone == "1":
            self._parent_device.send_command( \
                str(round(volume * self._max_volume)).zfill(3) + "VL")
        elif self._zone == "2":
            self._parent_device.send_command( \
                str(round(volume * self._max_volume)).zfill(2) + "ZV")
        elif self._zone == "3":
            self._parent_device.send_command( \
                str(round(volume * self._max_volume)).zfill(2) + "YV")
        elif self._zone == "Z":
            self._parent_device.send_command( \
                str(round(volume * self._max_volume)).zfill(2) + "HZV")
        else:
            _LOGGER.warning("Invalid service request for zone %s", self._zone)

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        if self._zone == "1":
            self._parent_device.send_command("MO" if mute else "MF")
        elif self._zone == "2":
            self._parent_device.send_command("Z2MO" if mute else "Z2MF")
        elif self._zone == "3":
            self._parent_device.send_command("Z3MO" if mute else "Z3MF")
        elif self._zone == "Z":
            self._parent_device.send_command("HZMO" if mute else "HZMF")
        else:
            _LOGGER.warning("Invalid service request for zone %s", self._zone)

class PioneerDevice(PioneerZone):
    """Representation of a Pioneer device."""

    def __init__(self, name, host, port, timeout, slow_refresh, use_http):
        """Initialize the Pioneer device."""
        _LOGGER.debug("PioneerDevice() started")
        self._host = host
        self._port = port
        self._timeout = timeout
        self._slow_refresh = slow_refresh
        self._use_http = use_http
        self._init = False
        self._telnet = None
        self._zones_on = False
        self._next_refresh = 0
        self._http_failed = False
        self._refresh_error = 0
        self._update_source = None
        self._source_name_to_number = {}
        self._source_number_to_name = {}

        self.zones = { "1": self }
        PioneerZone.__init__(self, name, "1", self)

    def source_name_to_number(self, name):
        return self._source_name_to_number.get(name)

    def source_number_to_name(self, number):
        return self._source_number_to_name.get(number)

    def get_source_list(self):
        """List of available input sources."""
        return list(self._source_name_to_number.keys())

    def telnet_open(self):
        if not self._telnet:
            self._telnet = telnetlib.Telnet(self._host, self._port, self._timeout)
            _LOGGER.debug("Telnet connection opened")
        return True

    def telnet_close(self):
        if self._telnet:
            self._telnet.close()
            self._telnet = None
            _LOGGER.debug("Telnet connection closed")

    def telnet_request(self, command, expected_prefix, ignore_error=False):
        """Execute a command via telnet and return the response."""
        self.telnet_open()
        _LOGGER.debug("Sending telnet request %s", command)
        self._telnet.write(command.encode("ASCII") + b"\r")

        # The receiver will randomly send state change updates, make sure
        # we get the response we are looking for
        result = ""
        for _ in range(3):
            result = self._telnet.read_until(b"\r\n", timeout=0.2)\
                .decode("ASCII").strip()
            if result.startswith("E"):
                # Error message returned, abort
                if not ignore_error:
                    raise Exception("Error " + result)
                else:
                    _LOGGER.debug("%s command %s returned error %s", self._name, command, result)
                    return None

            if result.startswith(expected_prefix):
                _LOGGER.debug("%s command %s response %s", self._name, command, result)
                return result
        if not ignore_error:
            raise Exception("Unexpected response " + result)
        else:
            _LOGGER.debug("%s command %s returned unexpected response %s", self._name, command, result)
            return None

    def telnet_command(self, command):
        """Send a command to the device via the telnet interface."""
        try:
            self.telnet_open()
            _LOGGER.debug("Sending telnet command %s", command)
            self._telnet.write(command.encode("ASCII") + b"\r")
            self._telnet.read_very_eager()  # skip response
            self.telnet_close()
            return True
        except Exception as e:
            _LOGGER.error("%s command %s returned error %s", self._name, command, str(e))
            self.telnet_close()
            return False

    def telnet_update(self):
        """Update device via telnet interface."""
        for z in self.zones:
            zone = self.zones[z]
            try:
                if z == "1":
                    pwstate = (self.telnet_request("?P", "PWR") == "PWR0")
                    volume = int((self.telnet_request("?V", "VOL"))[3:])
                    muted = (self.telnet_request("?M", "MUT") == "MUT0")
                    source_id = (self.telnet_request("?F", "FN"))[2:]
                elif z == "2":
                    pwstate = (self.telnet_request("?AP", "APR") == "APR0")
                    volume = int((self.telnet_request("?ZV", "ZV"))[2:])
                    muted = (self.telnet_request("?Z2M", "Z2MUT") == "Z2MUT0")
                    source_id = (self.telnet_request("?ZS", "Z2F"))[3:]
                elif z == "3":
                    pwstate = (self.telnet_request("?BP", "BPR") == "BPR0")
                    volume = int((self.telnet_request("?YV", "YV"))[2:])
                    muted = (self.telnet_request("?Z3M", "Z3MUT") == "Z3MUT0")
                    source_id = (self.telnet_request("?ZT", "Z3F"))[3:]
                elif z == "Z":
                    pwstate = (self.telnet_request("?ZEP", "ZEP") == "ZEP0")
                    volume = int((self.telnet_request("?HZV", "XV"))[2:])
                    muted = (self.telnet_request("?HZM", "HZMUT") == "HZMUT0")
                    source_id = (self.telnet_request("?ZEA", "ZEA"))[3:]
                else:
                    _LOGGER.warning("Invalid update request for zone %s", z)
                    pwstate = None
            except Exception as e:
                self.telnet_close()
                raise Exception("%s telnet update returned error %s" % (self._name, str(e)))
            if not pwstate == None:
                zone._available = True
                zone._pwstate = pwstate
                zone._volume = volume
                zone._muted = muted
                zone._source = self.source_number_to_name(source_id)
            if pwstate:
                self._zones_on = True
            if self._init and z != '1':
                zone.schedule_update_ha_state()
            _LOGGER.debug("Telnet update Zone %s: P:%s V:%d M:%s S:%s", z, pwstate, volume, muted, zone._source)
        self._update_source = 'telnet'
        return True

    def http_command(self, command):
        """Send a command to the device via the HTTP interface."""
        try:
            _LOGGER.debug("Sending http command %s", command)
            r = requests.post(
                'http://' + self._host + '/EventHandler.asp',
                data={ 'WebToHostItem': command },
                timeout=SCAN_INTERVAL.total_seconds()
            )
            if r.status_code != 200:
                _LOGGER.error("%s command %s returned error %d", self._name, command, r.status_code)
                return False
            return True
        except Exception as e:
            _LOGGER.error("%s command %s returned error %s", self._name, command, str(e))
            return False

    def http_get_status(self):
        """Request device status via the HTTP interface."""
        try:
            _LOGGER.debug("Sending http status request")
            r = requests.get(
                'http://' + self._host + '/StatusHandler.asp',
                timeout=self._timeout
            )
        except Exception as e:
            raise Exception("%s status update exception: %s" % (self._name, str(e)))
        if r.status_code != 200:
            raise Exception("%s status update returned error %d" % (self._name, command, r.status_code))
        return r.json()

    def http_update(self):
        """Update device via HTTP interface."""
        try:
            status = self.http_get_status()
        except:
            raise

        zone_status = reduce(lambda n, i: dict(n, **i), status['Z'])
        for z in self.zones:
            zone = self.zones[z]
            zone_data = zone_status.get(ZONE_JSON_MAP[z])
            if zone_data:
                zone._available = True
                zone._pwstate = (zone_data['P'] == 1)
                zone._volume = zone_data['V']
                zone._muted = (zone_data['M'] == 1)
                zone._source = self.source_number_to_name(
                    str(zone_data['F']).zfill(2)
                )
                if zone._pwstate:
                    self._zones_on = True
                if self._init and zone != '1':
                    zone.schedule_update_ha_state()
                _LOGGER.debug("HTTP update Zone %s: P:%s V:%d M:%s S:%s", z, zone._pwstate, zone._volume, zone._muted, zone._source)
            else:
                _LOGGER.warning("Invalid update request for zone %s", z)
        self._update_source = 'http'
        return True

    def send_request(self, command, expected_prefix, ignore_error=False):
        """Execute a command and return the response."""
        ## No http method to execute command, use telnet
        return self.telnet_request(command, expected_prefix, ignore_error)

    def send_command(self, command):
        """Send a command to the device."""
        if self._use_http and not self._http_failed:
            if self.http_command(command):
                return True
            else:
                _LOGGER.warning("%s command %s http failed, trying telnet", self._name, command)
        return self.telnet_command(command)

    def init_update(self):
        """Perform initial update."""

        _LOGGER.debug("Running initial update", self._name)
        try:
            ## Determine which zones are available by querying zone power status
            if self.send_request("?AP", "APR", ignore_error=True):
                self.zones["2"] = PioneerZone(self._name + " Zone 2", "2", self)
                _LOGGER.debug("Created Zone 2 object %x", id(self.zones["2"]))
    
            if self.send_request("?BP", "BPR", ignore_error=True):
                self.zones["3"] = PioneerZone(self._name + " Zone 3", "3", self)
                _LOGGER.debug("Created Zone 3 object %x", id(self.zones["3"]))
    
            if self.send_request("?ZEP", "ZEP", ignore_error=True):
                self.zones["Z"] = PioneerZone(self._name + " HDZone", "Z", self)
                _LOGGER.debug("Created HDZone object %x", id(self.zones["Z"]))

            ## Build the source name dictionaries
            for i in range(MAX_SOURCE_NUMBERS):
                result = self.send_request("?RGB" + str(i).zfill(2),
                    "RGB", ignore_error=True)
    
                if not result:
                    continue
    
                source_name = result[6:]
                source_active = (result[5] == '1')
                source_number = str(i).zfill(2)
    
                if (source_active):
                    self._source_name_to_number[source_name] = source_number
                    self._source_number_to_name[source_number] = source_name
            _LOGGER.debug("Source name->num: %s", self._source_name_to_number)
            _LOGGER.debug("Source num->name: %s", self._source_number_to_name)
            return True
        except Exception as e:
            _LOGGER.error("%s status update exception: %s", self._name, str(e))
            return False

    def set_refresh(self):
        """Check whether any zones are on, if not set slow refresh interval."""
        if self._refresh_error:
            _LOGGER.warning("%s is now available", self._name)
        self._refresh_error = 0
        if not self._zones_on:
            self._next_refresh = self._slow_refresh

    def update(self):
        """Get the latest details from the device."""

        ## Run initial update if first run
        if not self._init:
            if not self.init_update():
                self.telnet_close()
                return False

        ## Skip current refresh if slow refresh
        if self._next_refresh > 0:
            _LOGGER.debug("Skip refresh, next refresh in %d", self._next_refresh)
            self._next_refresh -= 1
            return True

        ## Perform HTTP update if HTTP is enabled
        self._zones_on = False
        if self._use_http:
            try:
                if self.http_update():
                    self.set_refresh()
                    self.telnet_close()
                    self._init = True
                    if self._http_failed:
                        _LOGGER.warning("%s reverting to http update", self._name)
                    self._http_failed = False
                    return True
            except Exception as e:
                if not self._http_failed:
                    _LOGGER.error(str(e))
            if not self._http_failed:
                _LOGGER.warning("%s http update failed, trying telnet", self._name)
            self._http_failed = True

        ## Perform telnet update
        try:
            if self.telnet_update():
                self.set_refresh()
                self.telnet_close()
                self._init = True
                return True
        except Exception as e:
            if self._refresh_error == 0:
                _LOGGER.error(str(e))

        ## Status is unavailable
        self._refresh_error += 1
        if self._refresh_error < DEFAULT_REFRESH_MAX_RETRIES:
            _LOGGER.warning("%s update failed #%d, retrying", self._name, self._refresh_error)
            self.telnet_close()
            return True

        ## Mark all zones unavailable
        if self._refresh_error == DEFAULT_REFRESH_MAX_RETRIES:
            _LOGGER.error("%s update failed max times #%d, marking zones unavailable", self._name, self._refresh_error)
        for z in self.zones:
            zone = self.zones[z]
            zone._available = False
            if self._init and zone != '1':
                zone.schedule_update_ha_state()
        self._next_refresh = self._slow_refresh
        self.telnet_close()
        self._init = True
        return False

    @property
    def should_poll(self):
        """Polling required for device, which will poll all zone data as well."""
        return True
