
import logging
from typing import Dict, Set

_LOGGER = logging.getLogger(__name__)


from .serial import CommeoSerialManager
import json


class SetupManager():
    def __init__(self, hass, serialManager, async_setup_finished):
        self.hass = hass
        self.actors = dict()
        self.initializationCompleted = False
        self.initializedActors: Set(str) = set[str]()
        self.partialInitializedActors:Set(str) = set[str]()
        self.uninitializedActors:Set(str) = set[str]()
        self.serialManager:CommeoSerialManager = serialManager
        self.serialManager.setEventHandlers(self.eventActorsReceived, self.eventActorInitialised, self.eventActorUpdate, self.eventMessageReceivingDone)
        self.async_setup_finished = async_setup_finished
        
    def eventMessageReceivingDone(self, isTimeout):
        if not isTimeout:
            self.hass.async_create_task(self.serialManager.recvMessage())

    def eventActorsReceived(self):
        if self.getAllActors() != self.serialManager.availableActors:
            self.uninitializedActors = self.serialManager.availableActors.difference(self.getAllActors())
        _LOGGER.info("New Available Actors %s" % self.uninitializedActors)
        for nextActor in self.uninitializedActors: 
            self.hass.async_create_task(self.serialManager.requestActorInfo(nextActor))

    def getAllActors(self):
        return self.initializedActors.union(self.uninitializedActors, self.partialInitializedActors)

    def eventActorInitialised(self, actorID:str):
        self.uninitializedActors.discard(actorID)
        self.partialInitializedActors.add(actorID)
        _LOGGER.info("Partial Initialized: %s" % actorID)
        self.hass.async_create_task(self.serialManager.requestShutterStatus(actorID))

    def eventActorUpdate(self, updatedActorID:str, isCreate):
        self.uninitializedActors.discard(updatedActorID)
        self.partialInitializedActors.discard(updatedActorID)
        self.initializedActors.add(updatedActorID)
        if isCreate:
            _LOGGER.info("Fully Initialized: %s" % updatedActorID)
        if not isCreate and self.initializationCompleted:
            self.actors[updatedActorID].async_schedule_update_ha_state()
        elif not self.initializationCompleted and self.getAllActors() == self.initializedActors:
            self.async_setup_finished(self.initializedActors)
            self.initializationCompleted = True