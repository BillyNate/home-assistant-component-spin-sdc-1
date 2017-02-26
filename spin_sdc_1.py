"""Support for the SPIN SDC 1 Remote

SPIN Remote is a device to remote control all kind of appliances.
It does this through its motions sensor and touchpad,
it has built-in Infra Red and Bluetooth.
Unfortunately the SPIN company hasn't disclosed an IR API, so we won't be able to control this part,
but setting up a Bluetooth connection and listen for notifications has been disclosed, so we can use the remote as a sensor now!
Hopefully the API will be extended to support IR soon ;)

This platform will only support one SPIN SDC 1 at the moment,
supoort for multiple SPINs may be added later.
"""

import asyncio
import homeassistant.util.dt as dt_util
import logging
import os
import re
import struct
import time
from datetime import timedelta
from bluepy.btle import DefaultDelegate, UUID
from homeassistant.const import CONF_ID, EVENT_HOMEASSISTANT_START, EVENT_HOMEASSISTANT_STOP
from homeassistant.config import load_yaml_config_file
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval


DISCOVERY_UUID = UUID("9DFACA9D-7801-22A0-9540-F0BB65E824FC")
SPIN_SERVICE_UUID = UUID("5E5A10D3-6EC7-17AF-D743-3CF1679C1CC7")
COMMAND_CHARACTERISTIC_UUID = UUID("92E92B18-FA20-D486-5E43-099387C61A71")
ACTION_CHARACTERISTIC_UUID = UUID("182BEC1F-51A4-458E-4B48-C431EA701A3B")
PROFILE_ID_CHARACTERISTIC_UUID = UUID("703fe135-0056-7398-1c4f-42e1636c2fd8")
UUID_CLIENT_CHARACTERISTIC_CONFIG = UUID("00002902-0000-1000-8000-00805f9b34fb")

DOMAIN = 'spin_sdc_1'

EVENT_SPIN_NOTIFICATION_RECEIVED = 'spin_notification_received'

REQUIREMENTS = ['http://github.com/IanHarvey/bluepy/archive/586c284b8b332def7d7cf397e06d2a5fdeb4ac14.zip#bluepy==1.0.5']

ATTR_DEVICE = 'device'
DEFAULT_DEVICE = 0
ATTR_PROFILE = 'profile'
DEFAULT_PROFILE = 'profile_0'
ATTR_SCAN_INTERVAL = 'scan_interval'
DEFAULT_SCAN_INTERVAL = 30.0
ATTR_SCAN_TIMEOUT = 'scan_timeout'
DEFAULT_SCAN_TIMEOUT = 10.0

_LOGGER = logging.getLogger(__name__)

ACTION_TO_STRING = [
    'rotate_right_side_up_clockwise',
    'rotate_right_side_up_counterclockwise',
    'rotate_sideways_clockwise',
    'rotate_sideways_counterclockwise',
    'rotate_upside_down_clockwise',
    'rotate_upside_down_counterclockwise',
    'touchpad_swipe_up',
    'touchpad_swipe_down',
    'touchpad_swipe_left',
    'touchpad_swipe_right',
    'touchpad_press_north',
    'touchpad_press_south',
    'touchpad_press_east',
    'touchpad_press_west',
    'touchpad_press_center',
    'touchpad_long_press_north',
    'touchpad_long_press_south',
    'touchpad_long_press_east',
    'touchpad_long_press_west',
    'touchpad_long_press_center',
    'touchpad_scroll_clockwise',
    'touchpad_scroll_counterclockwise',
    'reserved',
    'reserved',
    'spin_wake_up'
]

@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Setup SPIN remote(s)."""

    from bluepy.btle import Scanner, Peripheral, BTLEException

    bl_dev = config.get(ATTR_DEVICE, DEFAULT_DEVICE)
    scan_interval = config.get(ATTR_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    scan_timeout = config.get(ATTR_SCAN_TIMEOUT, DEFAULT_SCAN_TIMEOUT)
    checking_devices = False
    connected_to_device = False
    homeassistant_stopped = False
    known_device_adresses = []
    spins = {}

    # Would be a nice moment to check if bl_dev is even valid.

    @asyncio.coroutine
    def start_receiving_notifications(hass, peripheral):
        """Loop to receive notifications"""
        nonlocal checking_devices
        nonlocal connected_to_device
        nonlocal homeassistant_stopped

        while not homeassistant_stopped:
            try:
                hasNotification = yield from hass.loop.run_in_executor(None, peripheral.waitForNotifications, 1.0)
            except (BTLEException, AttributeError) as error:
                _LOGGER.warning(error)
                checking_devices = False
                connected_to_device = False

            if not connected_to_device:
                break

    @asyncio.coroutine
    def async_handle_spin(device, peripheral=None):
        """Prepare SPIN remote to be used by HASS"""
        nonlocal connected_to_device
        nonlocal spins
        global COMMAND_CHARACTERISTIC_UUID
        global ACTION_CHARACTERISTIC_UUID
        global PROFILE_ID_CHARACTERISTIC_UUID
        global UUID_CLIENT_CHARACTERISTIC_CONFIG

        if not peripheral:
            try:
                peripheral = yield from hass.loop.run_in_executor(None, Peripheral, device)
            except BTLEException as error:
                _LOGGER.warning(error)

        if peripheral:
            connected_to_device = True
            services = yield from hass.loop.run_in_executor(None, peripheral.getServices)

            for service in services:
                if service.uuid == SPIN_SERVICE_UUID:
                    actionCharacteristic = service.getCharacteristics(ACTION_CHARACTERISTIC_UUID)
                    profile_idCharacteristic = service.getCharacteristics(PROFILE_ID_CHARACTERISTIC_UUID)
                    commandCharacteristic = service.getCharacteristics(COMMAND_CHARACTERISTIC_UUID)

                    _LOGGER.info("Turning notifications on")
                    if actionCharacteristic:
                        descriptors = actionCharacteristic[0].getDescriptors(UUID_CLIENT_CHARACTERISTIC_CONFIG)
                        descriptors[0].write(struct.pack('<bb', 0x01, 0x00), True)

                    if profile_idCharacteristic:
                        profile_id = profile_idCharacteristic[0].read()
                        descriptors = profile_idCharacteristic[0].getDescriptors(UUID_CLIENT_CHARACTERISTIC_CONFIG)
                        descriptors[0].write(struct.pack('<bb', 0x01, 0x00), True)
                        spins[list(spins.keys())[0]]['entity'].profile_update(profile_id[0])
                    
                    if commandCharacteristic:
                        commandCharacteristic[0].write(struct.pack('<bb', 0x08, 0x01), True)

                    peripheral.withDelegate(NotificationDelegate(hass, spins))
                    hass.async_add_job(start_receiving_notifications, hass, peripheral)

    @asyncio.coroutine
    def async_new_device_found(device):
        """Check if the newly found BLE device is a SPIN"""
        nonlocal checking_devices
        nonlocal connected_to_device
        nonlocal known_device_adresses
        nonlocal spins
        global DISCOVERY_UUID
        global SPIN_SERVICE_UUID
        global COMMAND_CHARACTERISTIC_UUID

        _LOGGER.info("Checking " + device.addr)

        checking_devices = True

        try:
            peripheral = yield from hass.loop.run_in_executor(None, Peripheral, device)
            services = yield from hass.loop.run_in_executor(None, peripheral.getServices)

            if not device.addr in spins:
                # Walk through the list of services to see if one of them matches the DISCOVERY_UUID
                for service in services:
                    if service.uuid == DISCOVERY_UUID:
                        sdc1 = SDC1("Spin", "connected")
                        spins[device.addr] = { 'device': device, 'peripheral': peripheral, 'entity': sdc1 }
                        connected_to_device = True
                        yield from async_add_devices([sdc1])
                        _LOGGER.info("Connected to BLE device " + device.addr)
            else:
                spins[device.addr]['device'] = device
                spins[device.addr]['peripheral'] = peripheral

            known_device_adresses.append(device.addr)

            if connected_to_device == False:
                peripheral.disconnect()
            else:
                yield from async_handle_spin(device, peripheral)

        except BTLEException as error:
            _LOGGER.warning(error)
            checking_devices = False
            connected_to_device = False

    @asyncio.coroutine
    def async_on_time_interval(now: dt_util.dt.datetime):
        """Start scanning for BLE devices"""
        nonlocal scan_timeout
        nonlocal known_device_adresses
        nonlocal checking_devices
        nonlocal connected_to_device
        devices = []

        if not checking_devices and not connected_to_device:
            checking_devices = True
            _LOGGER.info("Scanning for BLE devices for " + str(scan_timeout) + " seconds")

            try:
                devices = yield from hass.loop.run_in_executor(None, scanner.scan, scan_timeout)
            except BTLEException as error:
                _LOGGER.warning(error)

            _LOGGER.info("Found " + str(len(devices)) + " BLE devices")
            for device in devices:
                if not device.addr in known_device_adresses:
                    yield from async_new_device_found(device)
                elif device.addr in spins:
                    _LOGGER.info("SPIN found, reconnecting...")
                    yield from async_handle_spin(device)
            checking_devices = False

    scanner = Scanner(bl_dev)

    # Because we sometimes get into trouble if we start searching to early; we'll start once Home Assistant is ready
    @asyncio.coroutine
    def async_on_homeassistant_start(event):
        """Once Home Assistant is started, we'll scan every 30 seconds or so"""
        nonlocal scan_interval
        interval = timedelta(seconds=scan_interval)
        remove_on_time_interval = async_track_time_interval(hass, async_on_time_interval, interval)
        hass.async_add_job(async_on_time_interval, None)

    @asyncio.coroutine
    def async_on_homeassistant_stop(event):
        """Once Home Assistant stops, prevent further futures from being created"""
        nonlocal homeassistant_stopped
        homeassistant_stopped = True

    # Add listeners:
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, async_on_homeassistant_start)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_on_homeassistant_stop)

    @asyncio.coroutine
    def async_handle_profile_service(call):
        """Handle profile service calls"""
        nonlocal spins
        profileRegex = r"profile_(\d+)"
        profile = call.data.get(ATTR_PROFILE, DEFAULT_PROFILE) # Default to current profile?
        match = re.search(profileRegex, profile)

        if match:
            peripheral = spins[list(spins.keys())[0]]['peripheral']
            services = yield from hass.loop.run_in_executor(None, peripheral.getServices)
            for service in services:
                if service.uuid == SPIN_SERVICE_UUID:
                    profile_idCharacteristic = service.getCharacteristics(PROFILE_ID_CHARACTERISTIC_UUID)
                    if profile_idCharacteristic:
                        profile_idCharacteristic[0].write(struct.pack('<b', int(match.group(1))), True)
                        spins[list(spins.keys())[0]]['entity'].profile_update(int(match.group(1)))

    @asyncio.coroutine
    def async_handle_color_service(call):
        """Handle LED color service calls"""
        nonlocal spins
        red, green, blue = call.data.get('rgb_color', [0, 0, 0])
        
        peripheral = spins[list(spins.keys())[0]]['peripheral']
        services = yield from hass.loop.run_in_executor(None, peripheral.getServices)
        for service in services:
            if service.uuid == SPIN_SERVICE_UUID:
                commandCharacteristic = service.getCharacteristics(COMMAND_CHARACTERISTIC_UUID)
                if commandCharacteristic:
                    if red + green + blue < 1:
                        commandCharacteristic[0].write(struct.pack('<b', 0x07), True) # Remove forced LED color
                    else:
                        commandCharacteristic[0].write(struct.pack('<bBBB', 0x09, red, green, blue), True)

    hass.services.async_register(DOMAIN, 'profile', async_handle_profile_service, description=None)
    hass.services.async_register(DOMAIN, 'rgb_color', async_handle_color_service, description=None)

    return True

class SDC1(Entity):
    """Representation of a SPIN-SDC-1."""

    def __init__(self, name, state):
        """Initialize the sensor."""
        self._name = name
        self._state = state

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    def action_notification(self, action):
        """Fire an event when an action notification has been received"""
        global EVENT_SPIN_NOTIFICATION_RECEIVED
        self.hass.bus.fire(EVENT_SPIN_NOTIFICATION_RECEIVED, { 'entity_id': self.entity_id, 'action': action })

    def profile_update(self, profile_id):
        """Set state based on profile_id"""
        self._state = 'profile_' + str(profile_id)
        self.hass.async_run_job(self.async_update_ha_state)


class NotificationDelegate(DefaultDelegate):
    """
    If a notification is received, it will be handled by the handleNotification def in here
    """
    def __init__(self, hass, spins):
        DefaultDelegate.__init__(self)
        self.hass = hass
        self.spins = spins

    def handleNotification(self, cHandle, data):
        global ACTION_TO_STRING

        if cHandle == 0x30: # Action
            self.spins[list(self.spins.keys())[0]]['entity'].action_notification(ACTION_TO_STRING[ord(data)])
        elif cHandle == 0x3c: # Profile change
            self.spins[list(self.spins.keys())[0]]['entity'].profile_update(data[0])