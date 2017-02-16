import asyncio
import homeassistant.util.dt as dt_util
import logging
import os
import struct
import time
from bluepy.btle import Scanner, DefaultDelegate, Peripheral, UUID, BTLEException
from datetime import timedelta
from homeassistant.config import load_yaml_config_file
from homeassistant.helpers.event import async_track_time_interval

DISCOVERY_UUID = UUID("9DFACA9D-7801-22A0-9540-F0BB65E824FC")
SPIN_SERVICE_UUID = UUID("5E5A10D3-6EC7-17AF-D743-3CF1679C1CC7")
COMMAND_CHARACTERISTIC_UUID = UUID("92E92B18-FA20-D486-5E43-099387C61A71")
ACTION_CHARACTERISTIC_UUID = UUID("182BEC1F-51A4-458E-4B48-C431EA701A3B")
UUID_CLIENT_CHARACTERISTIC_CONFIG = UUID("00002902-0000-1000-8000-00805f9b34fb")

DOMAIN = 'spin_sdc_1'

EVENT_SPIN_NOTIFICATION_RECEIVED = 'spin_notification_received'

REQUIREMENTS = ['http://github.com/IanHarvey/bluepy/archive/586c284b8b332def7d7cf397e06d2a5fdeb4ac14.zip#bluepy==1.0.5']

DEFAULT_DEVICE = 0
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

class NotificationDelegate(DefaultDelegate):
    """
    If a notification is received, it will be handled by the handleNotification def in here
    """
    def __init__(self, hass):
        DefaultDelegate.__init__(self)
        self.hass = hass

    def handleNotification(self, cHandle, data):
        global EVENT_SPIN_NOTIFICATION_RECEIVED
        global ACTION_TO_STRING
        self.hass.bus.fire(EVENT_SPIN_NOTIFICATION_RECEIVED, { 'action': ACTION_TO_STRING[ord(data)] })

@asyncio.coroutine
def async_setup(hass, config):
    """Setup SPIN remote(s)."""
    bl_dev = config[DOMAIN].get('device', DEFAULT_DEVICE)
    scan_timeout = config[DOMAIN].get('scan_timeout', DEFAULT_SCAN_TIMEOUT)
    checking_devices = False
    connected_to_device = False
    known_device_adresses = []
    spin_device_addresses = []
    spins = []

    # Would be a nice moment to check if bl_dev is even valid.

    @asyncio.coroutine
    def start_receiving_notifications(hass, peripheral):
        while True:
            try:
                hasNotification = yield from hass.loop.run_in_executor(None, peripheral.waitForNotifications, 1.0)
            except (BTLEException, AttributeError) as error:
                _LOGGER.warning(error)
                checking_devices = False
                connected_to_device = False

    @asyncio.coroutine
    def async_handle_spin(device, peripheral=None):
        nonlocal connected_to_device
        global COMMAND_CHARACTERISTIC_UUID
        global ACTION_CHARACTERISTIC_UUID
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
                    # Look for the characteristic to send commands to
                    commandCharacteristic = service.getCharacteristics(COMMAND_CHARACTERISTIC_UUID)
                    if commandCharacteristic:
                        for i in range(0, 3):
                            commandCharacteristic[0].write(struct.pack('<bBBB', 0x09, 0xFF, 0x00, 0x00), True)
                            time.sleep(.5)
                            commandCharacteristic[0].write(struct.pack('<b', 0x07), True)
                            time.sleep(.5)

                    # Look for the characteristic to receive actions from
                    actionCharacteristic = service.getCharacteristics(ACTION_CHARACTERISTIC_UUID)
                    if actionCharacteristic and commandCharacteristic:

                        _LOGGER.info("Turning notifications on")

                        # Get config descriptor
                        descriptors = actionCharacteristic[0].getDescriptors(UUID_CLIENT_CHARACTERISTIC_CONFIG)

                        # Enable notifications
                        commandCharacteristic[0].write(struct.pack('<bb', 0x08, 0x01), True)
                        descriptors[0].write(struct.pack('<bb', 0x01, 0x00), True)
                    
                    #yield from hass.loop.run_in_executor(None, start_receiving_notifications, hass, peripheral)
                    peripheral.withDelegate(NotificationDelegate(hass))
                    hass.async_add_job(start_receiving_notifications, hass, peripheral)

    @asyncio.coroutine
    def async_new_device_found(device):
        nonlocal checking_devices
        nonlocal connected_to_device
        nonlocal known_device_adresses
        nonlocal spin_device_addresses
        global DISCOVERY_UUID
        global SPIN_SERVICE_UUID
        global COMMAND_CHARACTERISTIC_UUID

        _LOGGER.info("Checking " + device.addr)

        checking_devices = True

        try:
            peripheral = yield from hass.loop.run_in_executor(None, Peripheral, device)
            services = yield from hass.loop.run_in_executor(None, peripheral.getServices)
            #peripheral = Peripheral(device)
            #services = peripheral.getServices()

            if not device.addr in spin_device_addresses:
                # Walk through the list of services to see if one of them matches the DISCOVERY_UUID
                for service in services:
                    if service.uuid == DISCOVERY_UUID:
                        spins.append({ 'device': device, 'peripheral': peripheral })
                        spin_device_addresses.append(device.addr)
                        connected_to_device = True
                        _LOGGER.info("Connected to BLE device " + device.addr)

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
                elif device.addr in spin_device_addresses:
                    _LOGGER.info("SPIN found, reconnecting...")
                    yield from async_handle_spin(device)
            checking_devices = False

    scanner = Scanner(bl_dev)

    # Because we sometimes get into trouble if we start searching to early; we'll start once Home Assistant is ready
    @asyncio.coroutine
    def async_on_homeassistant_start(event):
        interval = timedelta(seconds=30)
        remove_on_time_interval = async_track_time_interval(hass, async_on_time_interval, interval)
        hass.async_add_job(async_on_time_interval, None)

    hass.bus.async_listen_once('homeassistant_start', async_on_homeassistant_start)

    
    # This code is not async!:
#    if not spins:
#        hass.states.set('SPIN.Found', 'No devices')
#    else:
#        for i in range(len(spins)):
#            # create new panel for each SPIN remote
#            hass.states.set('SPIN.' + str(i), spins[i]['device'].addr)

    return True
