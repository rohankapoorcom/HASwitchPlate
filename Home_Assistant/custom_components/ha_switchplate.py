"""
An MQTT controlled user-programmable LCD touchscreen automation controller.

For more details about this controller, please refer to the documentation at
https://github.com/aderusha/HASwitchPlate

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/HASwitchPlate/
"""

import asyncio
import logging
from typing import List

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import HomeAssistantType

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'ha_switchplate'

DEPENDENCIES = ['mqtt']

CONF_NODE_NAME = 'nodes'
CONF_TOPIC_PREFIX = 'topic_prefix'

DEFAULT_TOPIC_PREFIX = 'hasp'

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_NODE_NAME): cv.ensure_list,
        vol.Optional(CONF_TOPIC_PREFIX, default=DEFAULT_TOPIC_PREFIX): cv.string
    })
}, extra=vol.ALLOW_EXTRA)

COMMAND_TOPIC_TEMPLATE = '{prefix}/{node}/command/'
STATE_TOPIC_TEMPLATE = '{prefix}/{node}/state/#'

PAYLOAD_ON = 'ON'
PAYLOAD_OFF = 'OFF'

EVENT_HASP_CONNECTED = 'ha_switchplate_connected'
EVENT_HASP_BUTTON = 'ha_switchplate_click'

ATTR_NODE_NAME = 'node_name'
ATTR_BUTTON_ID = 'button_id'
ATTR_BUTTON_ACTION = 'button_action'
ATTR_BACKGROUND_COLOR = 'background'
ATTR_FOREGROUND_COLOR = 'foreground'
ATTR_MESSAGE_TEXT = 'message'
ATTR_FONT_SIZE = 'font_size'
ATTR_UPDATE_FONT = 'update_font'

DEFAULT_FONT_PLACEHOLDER = -1

SERVICE_UPDATE_COLORS = 'update_colors'
SERVICE_UPDATE_MESSAGE = 'update_message'


class HASwitchPlate:
    """Representation of a HASwitchPlate controller"""

    def __init__(self, hass: HomeAssistantType, name: str, command_topic: str, state_topic: str):
        """Initialize the HASwitchPlate Controller"""
        self.hass = hass
        self._name = name
        self._command_topic = command_topic
        self._state_topic = state_topic

    @asyncio.coroutine
    def subscribe(self):
        yield from mqtt.async_subscribe(
            self.hass, self._state_topic, self.state_message_received)

    @callback
    def state_message_received(self, topic, payload, qos):
        """Handle a new received MQTT state message."""
        state_topic_prefix = self._state_topic[:-1]
        if not topic.startswith(state_topic_prefix):
            _LOGGER.warning('Node: %s subscribed to wrong topic: %s. Expected: %s',
                            self._name, topic, self._state_topic)

        if not (payload == PAYLOAD_ON or payload == PAYLOAD_OFF):
            _LOGGER.error('Unexpected payload: %s for node: %s on topic %s. Expected: %s or %s',
                          payload, self._name, self._state_topic, PAYLOAD_ON, PAYLOAD_OFF)

        button_id = topic[len(state_topic_prefix):]
        self.hass.bus.async_fire(EVENT_HASP_BUTTON, {
            ATTR_NODE_NAME: self._name,
            ATTR_BUTTON_ID: button_id,
            ATTR_BUTTON_ACTION: payload
        })


def _get_font_size(text):
    text_length = len(text)
    if text_length <= 6:
        return 3
    elif text_length <= 10:
        return 2
    elif text_length <= 15:
        return 1
    else:
        return 0


@asyncio.coroutine
async def _register_services(hass, topic_prefix):

    @callback
    def handle_update_colors(call):
        node_name: str = call.data.get(ATTR_NODE_NAME)
        button_id: str = call.data.get(ATTR_BUTTON_ID)
        background: str = call.data.get(ATTR_BACKGROUND_COLOR)
        foreground: str = call.data.get(ATTR_FOREGROUND_COLOR)

        if not (node_name and button_id and (background or foreground)):
            _LOGGER.error('%s requires %s, %s, and one of %s or %s to be provided',
                          SERVICE_UPDATE_COLORS, ATTR_NODE_NAME, ATTR_BUTTON_ID,
                          ATTR_BACKGROUND_COLOR, ATTR_FOREGROUND_COLOR)
            return

        base_topic = '{prefix}/{node_name}/command/{button_id}'.format(
            prefix=topic_prefix, node_name=node_name, button_id=button_id)

        if background:
            mqtt.publish(hass, '{}.bco'.format(base_topic), background)

        if foreground:
            mqtt.publish(hass, '{}.pco'.format(base_topic), background)

    @callback
    def handle_update_message(call):
        node_name: str = call.data.get(ATTR_NODE_NAME)
        button_id: str = call.data.get(ATTR_BUTTON_ID)
        message: str = call.data.get(ATTR_MESSAGE_TEXT)
        font_size: int = call.data.get(ATTR_FONT_SIZE, DEFAULT_FONT_PLACEHOLDER)
        update_font: bool = call.data.get(ATTR_UPDATE_FONT, True)

        if not (node_name and button_id and message):
            _LOGGER.error('%s requires %s, %s, and %s to be provided',
                          SERVICE_UPDATE_MESSAGE, ATTR_NODE_NAME, ATTR_BUTTON_ID,
                          ATTR_MESSAGE_TEXT)
            return

        base_topic = '{prefix}/{node_name}/command/{button_id}'.format(
            prefix=topic_prefix, node_name=node_name, button_id=button_id)

        actual_font_size = DEFAULT_FONT_PLACEHOLDER
        if update_font and font_size == DEFAULT_FONT_PLACEHOLDER:
            actual_font_size = _get_font_size(message)
        elif update_font != DEFAULT_FONT_PLACEHOLDER:
            actual_font_size = font_size

        mqtt.publish(hass, '{}.txt'.format(base_topic), '\"{}\"'.format(message))
        if actual_font_size != DEFAULT_FONT_PLACEHOLDER:
            mqtt.publish(hass, '{}.font'.format(base_topic), actual_font_size)

    hass.services.async_register(DOMAIN, SERVICE_UPDATE_COLORS, handle_update_colors)
    hass.services.async_register(DOMAIN, SERVICE_UPDATE_MESSAGE, handle_update_message)
    return True


@asyncio.coroutine
async def async_setup(hass, config):
    """Set up the HASwitchPlate."""

    config = config.get(DOMAIN)
    topic_prefix: str = config.get(CONF_TOPIC_PREFIX)
    nodes: List[str] = config.get(CONF_NODE_NAME)

    devices: List[HASwitchPlate] = []
    for node in nodes:
        command_topic = COMMAND_TOPIC_TEMPLATE.format(prefix=topic_prefix, node=node)
        state_topic = STATE_TOPIC_TEMPLATE.format(prefix=topic_prefix, node=node)
        devices.append(HASwitchPlate(hass, node, command_topic, state_topic))

    init = await initialize_nodes(devices)
    register = await _register_services(hass, topic_prefix)

    return init and register


async def initialize_nodes(devices: List[HASwitchPlate]) -> bool:
    for device in devices:
        await device.subscribe()
    return True
