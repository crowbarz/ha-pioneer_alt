""" API for Pioneer Network Receivers. """

# pylint: disable=logging-fstring-interpolation,broad-except

import logging

import traceback
import socket
import telnetlib
import time
import random
from threading import Thread, Event, Lock

_LOGGER = logging.getLogger(__name__)

MAX_VOLUME = 185
MAX_VOLUME_ZONEX = 81
MAX_SOURCE_NUMBERS = 60
RECONNECT_DELAY_MAX = 64

PIONEER_COMMANDS = {
    "turn_on": {"1": "PO", "2": "APO", "3": "BPO", "Z": "ZEO",},
    "turn_off": {"1": "PF", "2": "APF", "3": "BPF", "Z": "ZEF",},
    "select_source": {
        "1": ["FN", "FN"],
        "2": ["ZS", "Z2F"],
        "3": ["ZT", "Z3F"],
        "Z": ["ZEA", "ZEA"],
    },
    "volume_up": {
        "1": ["VU", "VOL"],
        "2": ["ZU", "ZV"],
        "3": ["YU", "YV"],
        "Z": ["HZU", "XV"],
    },
    "volume_down": {
        "1": ["VD", "VOL"],
        "2": ["ZD", "ZV"],
        "3": ["YD", "YV"],
        "Z": ["HZD", "XV"],
    },
    "set_volume_level": {
        "1": ["VL", "VOL"],
        "2": ["ZV", "ZV"],
        "3": ["YV", "YV"],
        "Z": ["HZV", "XV"],
    },
    "mute_on": {
        "1": ["MO", "MUT"],
        "2": ["Z2MO", "Z2MUT"],
        "3": ["Z3MO", "Z3MUT"],
        "Z": ["HZMO", "HZMUT"],
    },
    "mute_off": {
        "1": ["MF", "MUT"],
        "2": ["Z2MF", "Z2MUT"],
        "3": ["Z3MF", "Z3MUT"],
        "Z": ["HZMF", "HZMUT"],
    },
    "query_power": {
        "1": ["?P", "PWR"],
        "2": ["?AP", "APR"],
        "3": ["?BP", "BPR"],
        "Z": ["?ZEP", "ZEP"],
    },
    "query_volume": {
        "1": ["?V", "VOL"],
        "2": ["?ZV", "ZV"],
        "3": ["?YV", "YV"],
        "Z": ["?HZV", "XV"],
    },
    "query_mute": {
        "1": ["?M", "MUT"],
        "2": ["?Z2M", "Z2MUT"],
        "3": ["?Z3M", "Z3MUT"],
        "Z": ["?HZM", "HZMUT"],
    },
    "query_source_id": {
        "1": ["?F", "FN"],
        "2": ["?ZS", "Z2F"],
        "3": ["?ZT", "Z3F"],
        "Z": ["?ZEA", "ZEA"],
    },
}

## https://stackoverflow.com/questions/12248132/how-to-change-tcp-keepalive-timer-using-python-script
def sock_set_keepalive(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """Set TCP keepalive on an open socket.

    It activates after 1 second (after_idle_sec) of idleness,
    then sends a keepalive ping once every 3 seconds (interval_sec),
    and closes the connection after 5 failed ping (max_fails), or 15 seconds
    """
    if sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)


def get_backoff_delay(retry_count):
    """ Calculate exponential backoff with random jitter delay. """
    delay = round(
        min(RECONNECT_DELAY_MAX, (2 ** retry_count)) + (random.randint(0, 1000) / 1000),
        4,
    )
    return delay


class PioneerAVR:
    """ Pioneer AVR interface. """

    def __init__(
        self,
        host,
        port=8102,
        timeout=2,
        scan_interval=60,
        volume_workaround=True,
        command_delay=0.1,
    ):
        """ Initialize the Pioneer AVR interface. """
        _LOGGER.debug("PioneerAVR.__init__()")
        self._host = host
        self._port = port
        self._timeout = timeout
        self._scan_interval = scan_interval
        self._volume_workaround = volume_workaround
        self._command_delay = command_delay

        ## Public properties
        self.available = False
        self.zones = []
        self.power = {}
        self.volume = {}
        self.max_volume = {}
        self.mute = {}
        self.source = {}

        ## Internal state
        self._full_update = True
        self._last_updated = 0.0
        self._last_command = 0.0
        self._source_name_to_id = {}
        self._source_id_to_name = {}
        self._zone_callback = {}
        self._update_callback = None
        self._telnet_obj = None
        self._telnet_lock = Lock()
        self._telnet_thread = None
        self._telnet_reconnect = True
        self._request_lock = Lock()
        self._response_lock = Lock()
        self._response_event = Event()
        self._response_prefix = None
        self._response_value = None
        self._response_commands = []

        ## Connect to AVR and determine zones and sources
        self.telnet_connect()
        self.query_zones()
        self.build_source_dict()

    def __del__(self):
        _LOGGER.debug("PioneerAVR.__del__()")
        self.telnet_disconnect()

    def telnet_connect(self):
        """ Open telnet connection to AVR and start listener thread. """
        ## Open telnet connection
        _LOGGER.info(">> telnet_connect()")
        with self._telnet_lock:
            if not self._telnet_obj:
                try:
                    self._telnet_obj = telnetlib.Telnet(
                        self._host, self._port, self._timeout
                    )
                    sock_set_keepalive(self._telnet_obj.get_socket())
                    _LOGGER.info("AVR connection opened")
                except Exception as e:  # pylint: disable=invalid-name
                    raise Exception(f"Could not open AVR connection: {e}")
                self.available = True
                self._last_updated = 0.0
            else:
                _LOGGER.debug("AVR connection already open")

        ## Create child thread to listen to telnet socket
        if not self._telnet_thread or not self._telnet_thread.is_alive():
            try:
                _LOGGER.debug("Creating new AVR listener thread")
                self._telnet_thread = Thread(target=self.telnet_listener)
                if self._telnet_thread:
                    self._telnet_thread.start()
                    _LOGGER.debug("AVR listener started")
                else:
                    raise Exception("Could not create thread")
            except Exception as e:  # pylint: disable=invalid-name
                raise Exception(f"Could not start AVR listener: {e}")
        else:
            _LOGGER.debug("AVR listener already started")
        return True

    def telnet_disconnect(self, reconnect=True):
        """ Shutdown and close telnet connection to AVR. """
        _LOGGER.info(f">> telnet_disconnect(reconnect={reconnect})")
        with self._telnet_lock:
            if self._telnet_obj:
                _LOGGER.debug("Closing AVR connection")
                self._telnet_reconnect = reconnect
                self.available = False
                self.call_zone_callbacks()
                sock = self._telnet_obj.get_socket()
                if sock:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                self._telnet_obj.close()
                self._telnet_obj = None
                _LOGGER.info("AVR connection closed")

    def telnet_listener(self):
        """ Telnet listener thread. """
        _LOGGER.info("AVR listener running")
        while True:
            self.telnet_listener_main_loop()  # rc not checked

            ## Main loop exited, reconnect after delay
            _LOGGER.info(">> Calling telnet_disconnect()")
            self.telnet_disconnect()
            _LOGGER.info(">> Telnet disconnected")

            if not self._telnet_reconnect:
                _LOGGER.info("AVR reconnection disabled, not reconnecting")
                break
            _LOGGER.info("Reconnecting to AVR")
            retry = 0
            while True:
                delay = get_backoff_delay(retry)
                _LOGGER.debug(f"Waiting {delay}s before retrying connection")
                time.sleep(delay)
                retry += 1
                try:
                    self.telnet_connect()
                    ## Schedule update to be run in HA event loop
                    ## NOTE: Cannot run self.update() in listener thread as it
                    ## depends on the listener to process AVR responses
                    _LOGGER.debug("Scheduling full AVR status update")
                    self._full_update = True
                    self.call_update_callback()
                    break
                except Exception as e:  # pylint: disable=invalid-name
                    _LOGGER.debug(f"Could not reconnect to AVR: {e}")
                    self.telnet_disconnect()

        _LOGGER.info("AVR listener thread terminating")
        self.telnet_disconnect()

    def telnet_listener_main_loop(self):
        """ Main loop of telnet listener. """
        _LOGGER.debug("Running AVR listener main loop")
        while self._telnet_obj:
            try:
                ## Check for telnet responses
                raw_response = self._telnet_obj.read_until(b"\r\n")
                response = raw_response.decode().strip()
                self._last_updated = time.time()  ## include empty responses
                if not response:
                    _LOGGER.debug("Ignoring empty response")
                    ## Skip processing empty responses (keepalives?)
                    continue
                _LOGGER.debug(f"Received response: {response}")

                ## Parse response, update cached properties
                updated_zones = self.parse_response(response)
                if self._response_commands:
                    ## Send post-response commands
                    for cmd in self._response_commands:
                        self.telnet_send_command(cmd)
                    self._response_commands = []
                if updated_zones:
                    ## Call zone callbacks for updated zones
                    self.call_zone_callbacks(updated_zones)
                    ## NOTE: this does not seem to reset the scan interval
                    # if "1" not in updated_zones:
                    #     self.call_update_callback()  ## Reset scan interval timer
                    #     ## update will be skipped within timeout period

                ## Check whether a request is waiting for a response
                if self._response_lock.locked() and self._response_prefix:
                    if response.startswith("E"):
                        _LOGGER.debug(f"Signalling error {response} to waiting request")
                        self._response_value = response
                        self._response_event.set()
                    elif response.startswith(self._response_prefix):
                        _LOGGER.debug(
                            f"Signalling response {response} to waiting request"
                        )
                        self._response_value = response
                        self._response_event.set()

            except EOFError:
                _LOGGER.debug("AVR listener: EOFError")
                return False
            except TimeoutError:
                _LOGGER.debug("AVR listener: TimeoutError")
                return False
            except Exception as e:  # pylint: disable=invalid-name
                if not self.available:
                    ## Connection closed outside of listener
                    _LOGGER.debug(f"AVR listener exception: {e}")
                    return None
                else:
                    _LOGGER.error(f"AVR listener fatal exception: {e}")
                    traceback.print_exc()
                    _LOGGER.info(">> Exiting telnet_listener_main_loop()")
                    return None
        ## Telnet connection closed
        return False

    def telnet_send_command(self, command, rate_limit=True):
        """ Send a command to the AVR via telnet."""
        # _LOGGER.info(f">> telnet_send_command({command})")
        _LOGGER.debug(f"Sending AVR command {command}")

        ## Check if connection available
        if not self.available:
            raise Exception("AVR connection not available")

        now = time.time()
        if rate_limit:
            ## Rate limit commands
            since_command = now - self._last_command
            if since_command < self._command_delay:
                delay = self._command_delay - since_command
                _LOGGER.debug(f"Delaying command for {delay:.3f}s")
                time.sleep(self._command_delay - since_command)
        self._last_command = now

        try:
            self._telnet_obj.write(command.encode("UTF-8") + b"\r")
            return True
        except Exception as e:  # pylint: disable=invalid-name
            _LOGGER.error(f"Could not send AVR command: {str(e)}")
            self.telnet_disconnect()
            return False

    def telnet_send_request(
        self, command, response_prefix, ignore_error=None, rate_limit=True
    ):
        """ Execute a request synchronously on the AVR via telnet. """
        # _LOGGER.info(
        #     f">> telnet_send_request({command}, {response_prefix}, ignore_error={ignore_error})"
        # )
        with self._request_lock:  ## single request
            if not self.telnet_send_command(command, rate_limit=rate_limit):
                return False

            self._response_event.clear()
            self._response_prefix = response_prefix
            with self._response_lock:
                self._response_event.wait(timeout=self._timeout)
                self._response_prefix = None
                if self._response_event.is_set():
                    response = self._response_value
                    if response.startswith("E"):
                        ## Error value returned
                        err = f"AVR command {command} returned error {response}"
                        if ignore_error is None:
                            raise Exception(err)
                        elif not ignore_error:
                            _LOGGER.error(err)
                            return False
                        elif ignore_error:
                            _LOGGER.debug(err)
                            return False
                    return response
                ## Request timed out
                _LOGGER.debug(f"AVR command {command} returned no response")
                return None

    ## Raw functions. Hook for re-implementing HTTP command/requests.
    def send_raw_command(self, raw_command, rate_limit=True):
        """ Send a raw command to the device. """
        return self.telnet_send_command(raw_command, rate_limit)

    def send_raw_request(
        self, raw_command, response_prefix, ignore_error=None, rate_limit=True
    ):
        """ Execute a raw command and return the response. """
        return self.telnet_send_request(
            raw_command, response_prefix, ignore_error, rate_limit
        )

    def send_command(
        self, command, zone="1", prefix="", ignore_error=None, rate_limit=True
    ):
        """ Send a command or request to the device. """
        # pylint: disable=unidiomatic-typecheck
        raw_command = PIONEER_COMMANDS.get(command, {}).get(zone)
        if type(raw_command) is list:
            if len(raw_command) == 2:
                ## Handle command as request
                expected_response = raw_command[1]
                raw_command = raw_command[0]
                return self.send_raw_request(
                    prefix + raw_command, expected_response, ignore_error, rate_limit
                )
            else:
                _LOGGER.error(f"Invalid request {raw_command} for zone {zone}")
                return None
        elif type(raw_command) is str:
            return self.send_raw_command(prefix + raw_command, rate_limit)
        else:
            _LOGGER.warning(f"Invalid command {command} for zone {zone}")
            return None

    ## Initialisation functions
    def query_zones(self):
        """ Query zones on Pioneer AVR by querying power status. """
        if not self.zones:
            _LOGGER.info("Querying available zones on AVR")
            if self.send_command("query_power", "1", ignore_error=True):
                _LOGGER.info("Zone 1 discovered")
                if "1" not in self.zones:
                    self.zones.append("1")
                    self.max_volume["1"] = MAX_VOLUME
            else:
                raise RuntimeError("Main Zone not found on AVR")
            if self.send_command("query_power", "2", ignore_error=True):
                _LOGGER.info("Zone 2 discovered")
                if "2" not in self.zones:
                    self.zones.append("2")
                    self.max_volume["2"] = MAX_VOLUME_ZONEX
            if self.send_command("query_power", "3", ignore_error=True):
                _LOGGER.info("Zone 3 discovered")
                if "3" not in self.zones:
                    self.zones.append("3")
                    self.max_volume["3"] = MAX_VOLUME_ZONEX
            if self.send_command("query_power", "Z", ignore_error=True):
                _LOGGER.info("HDZone discovered")
                if "Z" not in self.zones:
                    self.zones.append("Z")
                    self.max_volume["Z"] = MAX_VOLUME_ZONEX

    def build_source_dict(self):
        """ Generate source id<->name translation tables. """
        timeouts = 0
        if not self._source_name_to_id:
            _LOGGER.info("Querying source names on AVR")
            for src in range(MAX_SOURCE_NUMBERS):
                response = self.send_raw_request(
                    "?RGB" + str(src).zfill(2),
                    "RGB",
                    ignore_error=True,
                    rate_limit=False,
                )
                if response is None:
                    timeouts += 1
                    _LOGGER.debug(f"Timeout {timeouts} retrieving source {src}")
                elif response is not False:
                    timeouts = 0
                    source_name = response[6:]
                    source_active = response[5] == "1"
                    source_number = str(src).zfill(2)
                    if source_active:
                        self._source_name_to_id[source_name] = source_number
                        self._source_id_to_name[source_number] = source_name
            _LOGGER.debug(f"Source name->id: {self._source_name_to_id}")
            _LOGGER.debug(f"Source id->name: {self._source_id_to_name}")
        if not self._source_name_to_id:
            raise RuntimeError("No input sources found on AVR")

    def get_source_list(self):
        """ Return list of available input sources. """
        return list(self._source_name_to_id.keys())

    ## Callback functions
    def set_zone_callback(self, zone, callback):
        """ Register a callback for a zone. """
        if zone in self.zones:
            if callback:
                self._zone_callback[zone] = callback
            else:
                self._zone_callback.pop(zone)

    def call_zone_callbacks(self, zones=None):
        """ Call callbacks to signal updated zone(s). """
        if zones is None:
            zones = self.zones
        for zone in zones:
            if zone in self._zone_callback:
                callback = self._zone_callback[zone]
                if callback:
                    _LOGGER.debug(f"Calling callback for zone {zone}")
                    callback()

    def set_update_callback(self, callback):
        """ Register a callback to trigger update. """
        if callback:
            self._update_callback = callback
        else:
            self._update_callback = None

    def call_update_callback(self):
        """ Trigger update. """
        if self._update_callback:
            _LOGGER.debug("Calling update callback")
            self._update_callback()

    ## Update functions
    def parse_response(self, response):
        """ Parse response and update cached parameters. """
        updated_zones = set()
        if response.startswith("PWR"):
            value = response == "PWR0"
            if self.power.get("1") != value:
                self.power["1"] = value
                updated_zones.add("1")
                _LOGGER.info(f"Zone 1: Power: {value}")
                if value and self._volume_workaround:
                    self._response_commands.extend(["VU", "VD"])
        elif response.startswith("APR"):
            value = response == "APR0"
            if self.power.get("2") != value:
                self.power["2"] = value
                updated_zones.add("2")
                _LOGGER.info(f"Zone 2: Power: {value}")
        elif response.startswith("BPR"):
            value = response == "BPR0"
            if self.power.get("3") != value:
                self.power["3"] = value
                updated_zones.add("3")
                _LOGGER.info(f"Zone 3: Power: {value}")
        elif response.startswith("ZEP"):
            value = response == "ZEP0"
            if self.power.get("Z") != value:
                self.power["Z"] = value
                updated_zones.add("Z")
                _LOGGER.info(f"HDZone: Power: {value}")

        elif response.startswith("VOL"):
            value = int(response[3:])
            if self.volume.get("1") != value:
                self.volume["1"] = value
                updated_zones.add("1")
                _LOGGER.info(f"Zone 1: Volume: {value}")
        elif response.startswith("ZV"):
            value = int(response[2:])
            if self.volume.get("2") != value:
                self.volume["2"] = value
                updated_zones.add("2")
                _LOGGER.info(f"Zone 2: Volume: {value}")
        elif response.startswith("YV"):
            value = int(response[2:])
            if self.volume.get("3") != value:
                self.volume["3"] = value
                updated_zones.add("3")
                _LOGGER.info(f"Zone 3: Volume: {value}")
        elif response.startswith("XV"):
            value = int(response[2:])
            if self.volume.get("Z") != value:
                self.volume["Z"] = value
                updated_zones.add("Z")
                _LOGGER.info(f"HDZone: Volume: {value}")

        elif response.startswith("MUT"):
            value = response == "MUT0"
            if self.mute.get("1") != value:
                self.mute["1"] = value
                updated_zones.add("1")
                _LOGGER.info(f"Zone 1: Mute: {value}")
        elif response.startswith("Z2MUT"):
            value = response == "Z2MUT0"
            if self.mute.get("2") != value:
                self.mute["2"] = value
                updated_zones.add("2")
                _LOGGER.info(f"Zone 2: Mute: {value}")
        elif response.startswith("Z3MUT"):
            value = response == "Z3MUT0"
            if self.mute.get("3") != value:
                self.mute["3"] = value
                updated_zones.add("3")
                _LOGGER.info(f"Zone 3: Mute: {value}")
        elif response.startswith("HZMUT"):
            value = response == "HZMUT0"
            if self.mute.get("Z") != value:
                self.mute["Z"] = value
                updated_zones.add("Z")
                _LOGGER.info(f"HDZone: Mute: {value}")

        elif response.startswith("FN"):
            raw_id = response[2:]
            value = self._source_id_to_name.get(raw_id, raw_id)
            if self.source.get("1") != value:
                self.source["1"] = value
                updated_zones.add("1")
                _LOGGER.info(f"Zone 1: Source: {value}")
        elif response.startswith("Z2F"):
            raw_id = response[3:]
            value = self._source_id_to_name.get(raw_id, raw_id)
            if self.source.get("2") != value:
                self.source["2"] = value
                updated_zones.add("2")
                _LOGGER.info(f"Zone 2: Source: {value}")
        elif response.startswith("Z3F"):
            raw_id = response[3:]
            value = self._source_id_to_name.get(raw_id, raw_id)
            if self.source.get("3") != value:
                value = self.source["3"]
                updated_zones.add("3")
                _LOGGER.info(f"Zone 3: Source: {value}")
        elif response.startswith("ZEA"):
            raw_id = response[3:]
            value = self._source_id_to_name.get(raw_id, raw_id)
            if self.source.get("Z") != value:
                self.source["Z"] = value
                updated_zones.add("Z")
                _LOGGER.info(f"HDZone: Source: {value}")
        return updated_zones

    def update_zone(self, zone):
        """ Update an AVR zone. """
        ## Check for timeouts, but ignore errors (eg. ?V will
        ## return E02 immediately after power on)
        if (
            self.send_command("query_power", zone, ignore_error=True) is None
            or self.send_command("query_volume", zone, ignore_error=True) is None
            or self.send_command("query_mute", zone, ignore_error=True) is None
            or self.send_command("query_source_id", zone, ignore_error=True) is None
        ):
            ## Timeout occurred, indicates AVR disconnected
            raise TimeoutError("Timeout waiting for data")

    def update(self):
        """ Update AVR cached status. """
        if self.available:
            now = time.time()
            since_updated = now - self._last_updated
            full_update = self._full_update
            if full_update or since_updated > self._scan_interval:
                _LOGGER.debug(
                    f"Updating AVR status (full={full_update}, last updated {since_updated:.3f}s ago)"
                )
                self._last_updated = now
                self._full_update = False
                try:
                    for zone in self.zones:
                        self.update_zone(zone)
                    if full_update:
                        ## Trigger updates to all zones on full update
                        self.call_zone_callbacks()
                    return True
                except Exception as e:  # pylint: disable=invalid-name
                    _LOGGER.error(f"Could not update AVR status: {e}")
                    self.telnet_disconnect()
                    return False
            else:
                ## NOTE: any response from the AVR received within
                ## scan_interval, including keepalives and responses triggered
                ## via the remote and by other clients, will cause an update to
                ## be skipped on the basis that the AVR is alive.
                ##
                ## Keepalives may be sent by the AVR (every 30 seconds on the
                ## VSX-930) when connected to port 8102, but are not sent when
                ## connected to port 23.
                _LOGGER.debug(f"Skipping update: last updated {since_updated:.3f}s ago")
                return True
        else:
            _LOGGER.debug("Skipping update: AVR is unavailable")
            return True

    ## State change functions
    def turn_on(self, zone="1"):
        """ Turn on the Pioneer AVR. """
        return self.send_command("turn_on", zone)

    def turn_off(self, zone="1"):
        """ Turn off the Pioneer AVR. """
        return self.send_command("turn_off", zone)

    def select_source(self, source, zone="1"):
        """ Select input source. """
        source_id = self._source_name_to_id.get(source)
        if source_id:
            return self.send_command(
                "select_source", zone, prefix=source_id, ignore_error=False
            )
        else:
            _LOGGER.error(f"Invalid source {source} for zone {zone}")
            return False

    def volume_up(self, zone="1"):
        """ Volume up media player. """
        return self.send_command("volume_up", zone, ignore_error=False)

    def volume_down(self, zone="1"):
        """ Volume down media player. """
        return self.send_command("volume_down", zone, ignore_error=False)

    def bounce_volume(self):
        """
        Send volume up/down to work around Main Zone reporting bug where
        an initial volume is set. This initial volume is not reported until
        the volume is changed.
        """
        if self.volume_up():
            return self.volume_down()
        else:
            return False

    def set_volume_level(self, volume, zone="1"):
        """ Set volume level (0..185 for Zone 1, 0..81 for other Zones). """
        if (
            volume < 0
            or (zone == "1" and volume > MAX_VOLUME)
            or (zone != "1" and volume > MAX_VOLUME_ZONEX)
        ):
            raise ValueError(f"volume {volume} out of range for zone {zone}")
        vol_len = 3 if zone == "1" else 2
        vol_prefix = str(volume).zfill(vol_len)
        return self.send_command(
            "set_volume_level", zone, prefix=vol_prefix, ignore_error=False
        )

    def mute_on(self, zone="1"):
        """ Mute AVR. """
        return self.send_command("mute_on", zone, ignore_error=False)

    def mute_off(self, zone="1"):
        """ Unmute AVR. """
        return self.send_command("mute_off", zone, ignore_error=False)


logging.basicConfig(level=logging.DEBUG)
