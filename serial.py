## Hassio imports
import logging
from typing import Dict, Set
from homeassistant.components.cover import (
    SUPPORT_OPEN,
    SUPPORT_CLOSE,
    SUPPORT_SET_POSITION,
    SUPPORT_STOP,
    ATTR_POSITION,
)

from homeassistant.const import (
    SERVICE_OPEN_COVER,
    SERVICE_CLOSE_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
    SERVICE_TOGGLE,
    SERVICE_OPEN_COVER_TILT,
    SERVICE_CLOSE_COVER_TILT,
    SERVICE_STOP_COVER_TILT,
    SERVICE_SET_COVER_TILT_POSITION,
    SERVICE_TOGGLE_COVER_TILT,
    STATE_OPEN,
    STATE_CLOSED,
    STATE_OPENING,
    STATE_CLOSING,
)

### Async

import asyncio
import serial_asyncio
import concurrent.futures
import serial

import time
import xmltodict
import json
import base64
import sys
import math

_LOGGER = logging.getLogger(__name__)

STOP_COMMAND = 0
DRIVE_UP_COMMAND = 1
DRIVE_DOWN_COMMAND = 2
DRIVE_POS_COMMAND = 7


MAX_DRIVE_POS_VALUE = 65535


class ShutterResponse:
    def __init__(self, manager, response):
        self.resp = response
        self.manager:CommeoSerialManager = manager
    
    @property
    def actorText(self) -> str:
        return self.resp.getString(1)

    @property
    def actorID(self) -> int:
        return self.resp.getInt(0)

    @property
    def radioAddress(self) -> int:
        return self.resp.getInt(1)

    @property
    def actorTyp(self) -> int:
        return self.resp.getInt(2)

    @property
    def actorStatus(self) -> int:
        return self.resp.getInt(3)

    @property
    def isActiveShutter(self):
        return self.actorTyp == 1 and self.actorStatus == 1

    async def driveUp(self):
        await self.manager.requestShutterCommand(self.actorID, DRIVE_UP_COMMAND, 0)

    async def driveDown(self):
        await self.manager.requestShutterCommand(self.actorID, DRIVE_DOWN_COMMAND, 0)

    async def stop(self):
        await self.manager.requestShutterCommand(self.actorID, STOP_COMMAND, 0)

    async def drivePos(self, pos):
        adjustedPos = math.ceil(pos * MAX_DRIVE_POS_VALUE / 100)
        await self.manager.requestShutterCommand(self.actorID, DRIVE_POS_COMMAND, adjustedPos)

    def __repr__(self):
        return "<ShutterResponse text:%s id:%s>" % (self.actorText, self.actorID)

class ShutterStatusResponse:
    def __init__(self, response):
        self.resp = response
        self.closingGapTolerance = 0.05

    @property
    def actorID(self) -> int:
        return self.resp.getInt(0)

    def isClosing(self):
        return self.resp.getInt(1) == 3

    def isOpening(self):
        return self.resp.getInt(1) == 2
    
    def isStill(self):
        return self.resp.getInt(1) == 1

    def isClosed(self):
        return self.isStill() and self.resp.getInt(3) == MAX_DRIVE_POS_VALUE and self.currentPositionNearlyClosed()

    def isFullyOpen(self):
        return self.isStill() and self.resp.getInt(3) == 0 and self.currentPositionNearlyFullyOpen()

    def currentPositionNearlyClosed(self):
        return self.resp.getInt(2) > (MAX_DRIVE_POS_VALUE - self.getToleranceValue())

    def currentPositionNearlyFullyOpen(self):
        return self.resp.getInt(2) < (0 + self.getToleranceValue())

    def getToleranceValue(self):
        return int(MAX_DRIVE_POS_VALUE*self.closingGapTolerance)

    def getCurrentPosition(self):
        """Return current position of cover.
        100 is closed, 0 is fully open.
        """
        if self.isClosed():
            return 100
        elif self.isFullyOpen():
            return 0
        else:
            return self.adjustValue(self.resp.getInt(2))

    def getTargetPosition(self):
        """Return current position of cover.
        100 is closed, 0 is fully open.
        """
        return self.adjustValue(self.resp.getInt(3))

    def adjustValue(self, commeoValue):
        return math.ceil(((commeoValue / MAX_DRIVE_POS_VALUE) * 100))

    def __repr__(self):
        return "<ShutterStatusResponse isClosing:%s isOpening:%s isStill:%s curPos:%s targetPos:%s>" % (self.isClosing(), self.isOpening(), self.isStill(), self.getCurrentPosition(), self.getTargetPosition())

class Response:
    RESP_KEY = "methodResponse"
    CALL_KEY = "methodCall"

    def __init__(self, xmlStr):
        self.content = xmltodict.parse(xmlStr)
        if self.RESP_KEY in self.content:
            self.rootKey = self.RESP_KEY
        elif self.CALL_KEY in self.content: 
            self.rootKey = self.CALL_KEY
        else:
            raise Exception(f'Unkown Response Format:\n{json.dumps(self.content)}\n')

    def isFault(self):
        return "fault" in self.content[self.rootKey]

    def getFaultMessage(self):
        return self.content[self.rootKey]["fault"]["array"]["string"]

    def getBase64(self, index):
        b64List = self.content[self.rootKey]["array"]["base64"]
        if isinstance(b64List, list):
            return self.base64ToIntSet(b64List[index])
        return self.base64ToIntSet(b64List)

    def base64ToIntSet(self, b64Str):
        mybyte = base64.b64decode(b64Str)
        byteNum = int.from_bytes(mybyte, byteorder="little")
        bitStr = "{0:b}".format(byteNum)
        bitStr=''.join(reversed(bitStr))
        intSet = {x for x in range(len(bitStr)) if bitStr[x] == '1'}
        _LOGGER.info(f"b64Str:{b64Str} - bitStr:{bitStr} - intSet:{intSet}")
        return intSet

    def getString(self, index):
        strList = self.content[self.rootKey]["array"]["string"]
        if isinstance(strList, list):
            return strList[index]
        return strList

    def getInt(self, index):
        intList = self.content[self.rootKey]["array"]["int"]
        if isinstance(intList, list):
            return int(intList[index])
        return int(intList)

    def getMethodName(self):
        if self.rootKey == self.RESP_KEY:
            return self.getString(0)
        else:
            return self.content[self.rootKey]["methodName"]

    def __repr__(self):
        return "<ShutterResponse response:%s>" % (json.dumps(self.content))


class CommeoSerialManager:
    def __init__(self, serialPort,):
        self.serialPort = serialPort
        self.availableActors = set()
        self.actorInfo:Dict(str, ShutterResponse) = dict()
        self.actorStatus:Dict[str, ShutterStatusResponse] = dict()
        self.isReading = False
        self.isWriting = False
        self.writingQueue = asyncio.Queue()
        self.readTimeout = 3
        self.writeTimeout = 0.08

    def setEventHandlers(self, eventActorsReceived, eventActorInitialised, eventActorUpdate, eventMessageReceivingDone):
        self.eventActorsReceived = eventActorsReceived
        self.eventActorInitialised = eventActorInitialised
        self.eventActorUpdate = eventActorUpdate
        self.eventMessageReceivingDone = eventMessageReceivingDone

    async def setup(self, loop):
        try:
            self.reader: asyncio.StreamReader
            self.writer: asyncio.StreamWriter
            self.reader, self.writer = await serial_asyncio.open_serial_connection(loop=loop,
                url=self.serialPort,
                baudrate=115200,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS)
        except Exception as e:            
            _LOGGER.error ('error open serial port: ' + str(e))
            raise "Serial Connection could not be opened!"
        _LOGGER.info("Serial Connection Opened!")

    async def send(self, msg):
        msg = msg+'\n\n'
        msg = msg.encode('ascii')
        
        await self.writingQueue.put(msg)
        if not self.isWriting:
            self.isWriting = True
            try:
                while not self.writingQueue.empty():
                    nextMsg = await self.writingQueue.get()
                    _LOGGER.info(f"--- Sent ---\n{nextMsg}\n")
                    self.writer.write(nextMsg)
                    await self.writer.drain()
                    await asyncio.sleep(self.writeTimeout)
            except Exception as err:
                _LOGGER.exception("error during send: %s" % str(err))
                _LOGGER.error("error-causing msg: %s" % msg)
            finally:
                self.isWriting = False        

    async def recvMessage(self):
        if self.isReading:
            return
        self.isReading = True
        msg = ''    
        try:
            msg = await asyncio.wait_for(self.reader.readuntil(b'\n\n'), self.readTimeout)
        except asyncio.TimeoutError:
            self.isReading = False
            _LOGGER.info("COMMEO Timeout")
            _LOGGER.info(f'--- Received ---\n{msg}')
            self.eventMessageReceivingDone(True)
            return
        except Exception as err:
            self.isReading = False
            _LOGGER.exception("error during recv: %s" % str(err))
            _LOGGER.error("error-causing block: %s" % msg)
            self.eventMessageReceivingDone(False)
            return
        self.isReading = False
        msg = msg.decode("utf-8")
        msg = msg[msg.find('<'):]
        myMsg = msg
        msg = ''
        self.eventMessageReceivingDone(False)
        #_LOGGER.info(f'--- Received ---\n{myMsg}')
        self.processMessage(myMsg)
        #_LOGGER.info(self)
        
    def processMessage(self, msg):
        resp = Response(msg)
        if resp.isFault():
            _LOGGER.error("Received FAULT: %s" % resp.getFaultMessage())
            return

        factory = {
           "selve.GW.device.getIDs": {"func": self.processActorIDs, "hasActor": False},
            "selve.GW.device.getInfo": {"func": self.processActorInfo, "hasActor": True},
            "selve.GW.command.device": {"func": self.discard, "hasActor": False},
            "selve.GW.command.result": {"func": self.processCommandResult, "hasActor": False},
            "selve.GW.device.getValues": {"func": self.processShutterStatus, "hasActor": True},
            "selve.GW.event.device": {"func": self.processShutterStatus, "hasActor": True},
            "selve.GW.event.dutyCycle": {"func": self.processDutyCycle, "hasActor": False},
            "selve.GW.event.log": {"func": self.processLog, "hasActor": False},
        }
        methodName = resp.getMethodName()
        if factory[methodName]["hasActor"]:
            _LOGGER.info(f'--- Received ---: {methodName} -- actorID: {resp.getInt(0)}')
        else:
            _LOGGER.info(f'--- Received ---: {methodName}')
        if methodName in factory:
            factory[methodName]["func"](resp)
        else:
            _LOGGER.info("Unkown methodName: %s" % methodName)

    def processCommandResult(self, resp):
        _LOGGER.info(f"Full Command Resp: {resp}")

        commandStr = {
            0: "STOP_COMMAND",
            1: "DRIVE_UP_COMMAND",
            2: "DRIVE_DOWN_COMMAND",
            7: "DRIVE_POS_COMMAND",
        }
        command = commandStr[resp.getInt(0)]
        isError = resp.getInt(2) == 0
        succeeded = resp.getBase64(0)
        failed = resp.getBase64(1)
        if len(succeeded) == 1:
            succeeded = succeeded.pop()

        if len(failed) == 1:
            failed = failed.pop()

        logStr = ''
        if len(failed) == 0:
            if isError:
                logStr = f'{command} completed with errors: actorID:{succeeded}'
            else:
                logStr = f'{command} completed: actorID:{succeeded}'
        elif len(succeeded) == 0:
            logStr = f'{command} FAILED: actorID:{failed}'
        else:
            logStr = f'{command}: failed_actorIDs:{failed}  succeeded_actorIDs:{succeeded}'

        if isError:
            _LOGGER.info(logStr)
        else:
            _LOGGER.error(logStr)

    def processDutyCycle(self, resp):
        isBlocked = resp.getInt(0) == 1
        radioAllowedUsage = resp.getInt(1)
        _LOGGER.info(f'Duty Cycle Informaiton: isBlocked:{isBlocked} radioAllowedUsage:{radioAllowedUsage}')

    def processLog(self, resp):
        status = resp.getInt(0) 
        statusTyp = ''
        if status == 0:
            statusType = "Info"
        elif status == 1:
            statusType = "Warning"
        else:
            statusType = "Error"
        logCode = resp.getString(0) 
        logValue = resp.getString(2) 
        logDescription = resp.getString(3) 
        _LOGGER.info(f'{statusType} Message - {logCode} - message: {logValue} -  description: {logDescription}')


    def processActorIDs(self, resp):
        self.availableActors = resp.getBase64(0)
        self.eventActorsReceived()

    def processActorInfo(self, raw):
        resp = ShutterResponse(self, raw)
        if resp.isActiveShutter:
            id = resp.actorID
            self.actorInfo[id] = resp
            self.eventActorInitialised(id)

    def processShutterStatus(self, raw):
        resp = ShutterStatusResponse(raw)
        id = resp.actorID
        isCreate = id not in self.actorStatus
        self.actorStatus[id] = resp
        self.eventActorUpdate(id, isCreate)
    
    def discard(self, resp):
        return
    
    async def requestActorIDs(self):
        await self.send('<methodCall><methodName>selve.GW.device.getIDs</methodName></methodCall>')
        
    async def requestActorInfo(self, actorID):
        await self.send('<methodCall><methodName>selve.GW.device.getInfo</methodName><array><int>%s</int></array></methodCall>' % (actorID))

    async def requestShutterCommand(self, actorID, command, parameter):
        _LOGGER.info('<methodCall><methodName>selve.GW.command.device</methodName><array><int>%s</int><int>%s</int><int>%s</int><int>%s</int></array></methodCall>' % (actorID, command, 1, parameter))
        await self.send('<methodCall><methodName>selve.GW.command.device</methodName><array><int>%s</int><int>%s</int><int>%s</int><int>%s</int></array></methodCall>' % (actorID, command, 1, parameter))

    async def requestShutterStatus(self, actorID):
        await self.send('<methodCall><methodName>selve.GW.device.getValues</methodName><array><int>%s</int></array></methodCall>' % (actorID))

    async def __repr__(self):
        return "<CommeoSerialManager \n\tavailableActors: %s\n\actorInfo: %s\n\actorStatus: %s\n>" % (self.availableActors, self.actorInfo, self.actorStatus )
