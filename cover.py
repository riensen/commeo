"""Example integration using DataUpdateCoordinator."""
from typing import Dict, Set
from datetime import timedelta
import logging
import json

import async_timeout
from homeassistant.config_entries import ConfigEntries, ConfigEntry

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.cover import (
    CoverEntity, 
    CoverDeviceClass,
    SUPPORT_OPEN,
    SUPPORT_CLOSE,
    SUPPORT_STOP,
    SUPPORT_SET_POSITION,
    ATTR_POSITION
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, CONF_DEVICE_PATH
from .serial import CommeoSerialManager, ShutterStatusResponse, ShutterResponse
from .setupmanager import SetupManager


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
):
    """Setup sensors from a config entry created in the integrations UI."""
    _LOGGER.info("entry.data: %s" % entry.data)

    serialDevice = str(entry.data)
    serialManager:CommeoSerialManager = CommeoSerialManager(serialDevice)

    def async_setup_finished(actors):
        _LOGGER.info("Starting Adding Entities")
        c = CommeoCoordinator(hass, serialManager)
        entities = list()
        for actorID in actors:
            shutter = CommeoEntity(c, actorID)
            #self.actors[actorID] = shutter
            entities.append(shutter)
            c.add_entity(actorID, shutter)
        entities.sort(key=lambda x: x.name)
        async_add_entities(entities)
        _LOGGER.info(f"All actors have been added: {entities}")

        

    SetupManager(hass, serialManager, async_setup_finished)
    await serialManager.setup(hass.loop)

    await serialManager.recvMessage()
    _LOGGER.info("Serial Setup")
    await serialManager.requestActorIDs()
    await serialManager.recvMessage()

"""
    # Fetch initial data so we have data when entities subscribe
    #
    # If the refresh fails, async_config_entry_first_refresh will
    # raise ConfigEntryNotReady and setup will try again later
    #
    # If you do not want to retry setup on failure, use
    # coordinator.async_refresh() instead
    #
    await coordinator.async_config_entry_first_refresh()
"""

class CommeoCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass, manager):
        """Initialize my coordinator."""
        super().__init__(hass,_LOGGER,
            # Name of the data. For logging purposes.
            name="commeo",
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=1),
        )
        self.actors = dict[int, CommeoEntity]()
        self.manager:CommeoSerialManager = manager
        self.manager.setEventHandlers(self.eventActorsReceived, self.eventActorInitialised, self.eventActorUpdate, self.eventMessageReceivingDone)

    def getActor(self, actorID) -> ShutterResponse:
        return self.manager.actorInfo[actorID]

    def getActorStatus(self, actorID) -> ShutterStatusResponse:
        return self.manager.actorStatus[actorID]

    def add_entity(self, actorID:int, shutter):
        self.actors[actorID] = shutter

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.info(f"Try Receiving")
        await self.manager.recvMessage()

    def eventMessageReceivingDone(self, isTimeout):
        return

    def eventActorsReceived(self):
        return

    def eventActorInitialised(self, actorID:str):
        return

    def eventActorUpdate(self, updatedActorID:str, isCreate):
        _LOGGER.info(f"Try Updating actor {updatedActorID}")
        if not isCreate:
            _LOGGER.info(f"Updating actor {updatedActorID}")
            self.actors[updatedActorID].async_schedule_update_ha_state()
            
class CommeoEntity(CoordinatorEntity, CoverEntity):
    """An entity using CoordinatorEntity.

    The CoordinatorEntity class provides:
      should_poll
      async_update
      async_added_to_hass
      available

    """

    def __init__(self, coordinator:CommeoCoordinator, actorID):
        """Pass coordinator to CoordinatorEntity."""
        super().__init__(coordinator)
        self.actorID = actorID
        self.actor:ShutterResponse = coordinator.getActor(actorID)
        self._attr_name = self.actor.actorText
        self._attr_unique_id = self.actor.radioAddress
        self.update_attr()

    def update_attr(self):
        actorStatus:ShutterStatusResponse = self.coordinator.getActorStatus(self.actorID)
        adjPos = CommeoEntity.reversePosition(actorStatus.getCurrentPosition())

        self._attr_current_cover_position = adjPos
        self._attr_device_class = CoverDeviceClass.SHUTTER
        self._attr_is_closed= actorStatus.isClosed()
        self._attr_is_closing= actorStatus.isClosing()
        self._attr_is_opening= actorStatus.isOpening()
        
    @staticmethod
    def reversePosition(pos):
        return 100 - pos

    @property
    def supported_features(self):
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_STOP | SUPPORT_SET_POSITION

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update_attr()
        self.async_write_ha_state()


    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        _LOGGER.info("Driving UP///////////")
        return await self.actor.driveUp()

    async def async_close_cover(self, **kwargs):
        """Close cover."""
        _LOGGER.info("Driving Down///////////")
        return await self.actor.driveDown()

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        _LOGGER.info("setting Pos///////////")
        position = CommeoEntity.reversePosition(kwargs.get(ATTR_POSITION))
        return await self.actor.drivePos(position)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        _LOGGER.info("setting Stop///////////")
        return await self.actor.stop()