"""Config flow for Commeo Integration integration."""
from __future__ import annotations
import logging

from typing import Any

import voluptuous as vol
import json

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, CONF_DEVICE_PATH

_LOGGER = logging.getLogger(__name__)

import serial.tools.list_ports
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import usb
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResult

CONF_RADIO_TYPE = "radio_type"
CONF_MANUAL_PATH = "Enter Manually"
CONF_DEVICE = "device"


class CommeoFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 3

    def __init__(self):
        """Initialize flow instance."""
        self._device_path = None
        self._radio_type = None
        self._title = None

    async def async_step_user(self, user_input=None):
        """Handle a zha config flow start."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        ports = await self.hass.async_add_executor_job(serial.tools.list_ports.comports)
        list_of_ports = [
            f"{p}, s/n: {p.serial_number or 'n/a'}"
            + (f" - {p.manufacturer}" if p.manufacturer else "")
            for p in ports
        ]

        list_of_ports.append(CONF_MANUAL_PATH)

        if user_input is not None:
            user_selection = user_input[CONF_DEVICE_PATH]

            port = ports[list_of_ports.index(user_selection)]
            dev_path = await self.hass.async_add_executor_job(
                usb.get_serial_by_id, port.device
            )
            _LOGGER.info("Dev Path: %s" % dev_path)

            # did not detect anything
            return self.async_create_entry(title="Commeo Cover",data={"path": dev_path},
        )
            return self.async_create_entry(title=CONF_DEVICE, data=dev_path)

        schema = vol.Schema({vol.Required(CONF_DEVICE_PATH): vol.In(list_of_ports)})
        return self.async_show_form(step_id="user", data_schema=schema)


    async def async_step_usb(self, discovery_info: usb.UsbServiceInfo) -> FlowResult:
        """Handle usb discovery."""
        vid = discovery_info.vid
        pid = discovery_info.pid
        serial_number = discovery_info.serial_number
        device = discovery_info.device
        manufacturer = discovery_info.manufacturer
        description = discovery_info.description
        dev_path = await self.hass.async_add_executor_job(usb.get_serial_by_id, device)
        unique_id = f"{vid}:{pid}_{serial_number}_{manufacturer}_{description}"
        if current_entry := await self.async_set_unique_id(unique_id):
            self._abort_if_unique_id_configured(
                updates={
                    CONF_DEVICE: {
                        **current_entry.data.get(CONF_DEVICE, {}),
                        CONF_DEVICE_PATH: dev_path,
                    },
                }
            )
        # Check if already configured
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        self._device_path = dev_path
        self._title = usb.human_readable_device_name(
            dev_path,
            serial_number,
            manufacturer,
            description,
            vid,
            pid,
        )
        self._set_confirm_only()
        self.context["title_placeholders"] = {CONF_NAME: self._title}
        return await self.async_step_confirm()


    async def async_step_confirm(self, user_input=None):
        """Confirm a discovery."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._title,
                data=self._device_path,
            )

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={CONF_NAME: self._title},
            data_schema=vol.Schema({}),
        )