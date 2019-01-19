import asyncio
from timeit import default_timer as timer
import numpy as np
import uuid
import jsonpickle

class NodeException(Exception):
    def __init__(self, message, node, error):
        self.node = node
        self.error = error
        self.message = message
        super(NodeException, self).__init__(message)

class Node(object):

    def __init__(self, effect):
        self.effect = effect
        self.uid = None
        # TODO: Improve consistency with numInputChannels and numOutputChannels
        self.numInputChannels = 0
        self.numOutputChannels = 0
        self.__initstate__()
        self.numInputChannels = self.effect.numInputChannels()
        self.numOutputChannels = self.effect.numOutputChannels()

    def __initstate__(self):
        self._outputBuffer = [None for i in range(0, self.effect.numOutputChannels())]
        self._inputBuffer = [None for i in range(0, self.effect.numInputChannels())]
        self._incomingConnections = []

        self.effect.setOutputBuffer(self._outputBuffer)
        self.effect.setInputBuffer(self._inputBuffer)

    def process(self):
        # propagate values
        for con in self._incomingConnections:
            self._inputBuffer[con.toChannel] = con.fromNode._outputBuffer[con.fromChannel]
        # process
        self.effect.process()
    
    async def update(self, dt):
        await self.effect.update(dt)

    def __cleanState__(self, stateDict):
        """
        Cleans given state dictionary from state objects beginning with __
        """
        for k in list(stateDict.keys()):
            if k.startswith('_'):
                stateDict.pop(k)
        return stateDict
        
    def __getstate__(self):
        """
        Default implementation of __getstate__ that deletes buffer, call __cleanState__ when overloading
        """
        state = self.__dict__.copy()
        self.__cleanState__(state)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.__initstate__()

class Connection(object):

    def __init__(self, from_node, from_channel, to_node, to_channel):
        self.fromChannel = from_channel
        self.fromNode = from_node
        self.toChannel = to_channel
        self.toNode = to_node
        self.uid = None

    def __getstate__(self):
        state = {}
        state['from_node_uid'] = self.fromNode.uid
        state['from_node_channel'] = self.fromChannel
        state['to_node_uid'] = self.toNode.uid
        state['to_node_channel'] = self.toChannel
        state['uid'] = self.uid
        return state
        

class Timing(object):
    def __init__(self):
        self._max = None
        self._min = None
        self._avg = None
        self._count = 0
    
    def update(self, timing):
        if self._count == 0:
            self._max = timing
            self._min = timing
            self._avg = timing
        else:
            self._max = max(self._max, timing)
            self._min = min(self._min, timing)
            self._avg = (self._avg * self._count + timing) / (self._count + 1)
        self._count = self._count + 1
        self._count = min(100, self._count)


class FilterGraph(object):

    def __init__(self, recordTimings=False, asyncUpdate=True):
        self.recordTimings=recordTimings
        self.asyncUpdate=asyncUpdate
        self._filterConnections = []
        self._filterNodes = []
        self._processOrder = []
        self._updateTimings = {}
        self._processTimings = {}
        #self._asyncLoop = asyncio.get_event_loop()
        #self._asyncLoop = asyncio.new_event_loop()
        #asyncio.set_event_loop(self._asyncLoop)

    def update(self, dt, event_loop = asyncio.get_event_loop()):
        if self.asyncUpdate:
            time = timer()
            # gather all async updates
            asyncio.set_event_loop(event_loop)
            async def handle_async_exception(node, func, param):
                try:
                    await func(param)
                except Exception as e:
                    raise NodeException("{}".format(e), node, e)
            all_tasks = asyncio.gather(*[asyncio.ensure_future(handle_async_exception(node, node.update, dt)) for node in self._processOrder])
            # wait for completion
            event_loop.run_until_complete(all_tasks)
            self.updateUpdateTiming("all_async", timer() - time)
        else:
            for node in self._processOrder:
                try:
                    if self.recordTimings:
                        time = timer()
                    event_loop.run_until_complete(node.update(dt))
                    if self.recordTimings:
                        self.updateUpdateTiming(str(node.effect), timer() - time)
                except Exception as e:
                    raise NodeException("{}".format(e), node, e)
    

    def process(self):
        time = None

        for node in self._processOrder:
            try:
                if self.recordTimings:
                    time = timer()
                node.process()
                if self.recordTimings:
                    self.updateProcessTiming(node, timer() - time)
            except Exception as e:
                raise NodeException("{}".format(e), node, e)

    def updateProcessTiming(self,node,timing):
        if not node in self._processTimings:
            self._processTimings[node] = Timing()
        
        self._processTimings[node].update(timing)

    def updateUpdateTiming(self,node,timing):
        if not node in self._updateTimings:
            self._updateTimings[node] = Timing()
        
        self._updateTimings[node].update(timing)

    def printUpdateTimings(self):
        if self._updateTimings is None:
            print("No metrics collected")
            return
        print("Update timings:")
        for key, val in self._updateTimings.items():
            print("{0:30s}: min {1:1.8f}, max {2:1.8f}, avg {3:1.8f}".format(key[0:30], val._min, val._max, val._avg))
    
    def printProcessTimings(self):
        if self._processTimings is None:
            print("No metrics collected")
            return
        print("Process timings:")
        for key, val in self._processTimings.items():
            print("{0:30s}: min {1:1.8f}, max {2:1.8f}, avg {3:1.8f}".format(str(key.effect)[0:30], val._min, val._max, val._avg))


    def addEffectNode(self, effect):
        """Adds a filter node to the graph

        Parameters
        ----------
        filterNode: node to add
        """
        print("add node {}".format(effect))
        node = Node(effect)
        node.uid = uuid.uuid4().hex
        self._filterNodes.append(node)
        self._updateProcessOrder()
        return node

    def removeEffectNode(self, effect):
        """Removes a filter node from the graph

        Parameters
        ----------
        filterNode: node to remove
        """
        # Remove connections
        connections = [con for con in self._filterConnections if con.fromNode.effect == effect or con.toNode.effect == effect]
        for con in connections:
            self._filterConnections.remove(con)
        # Remove Node
        node = next(node for node in self._filterNodes if node.effect == effect)
        if node != None:
            self._filterNodes.remove(node)
            self._processOrder.remove(node)
        

    def addConnection(self, fromEffect, fromEffectChannel, toEffect, toEffectChannel):
        """Adds a connection between two filters
        """
        # find fromNode
        fromNode = next(node for node in self._filterNodes if node.effect == fromEffect)
        # find toNode
        toNode = next(node for node in self._filterNodes if node.effect == toEffect)
        # construct connection
        newConnection = Connection(fromNode, fromEffectChannel, toNode, toEffectChannel)
        newConnection.uid = uuid.uuid4().hex
        self._filterConnections.append(newConnection)
        toNode._incomingConnections.append(newConnection)
        self._updateProcessOrder()
        return newConnection

    def addNodeConnection(self, fromNodeUid, fromEffectChannel, toNodeUid, toEffectChannel):
        """Adds a connection between two filters based on node uid
        """
        print("add node connection from {} channel {} to {} channel {}".format(fromNodeUid,fromEffectChannel, toNodeUid, toEffectChannel))
        fromNode = next(node for node in self._filterNodes if node.uid == fromNodeUid)
        toNode = next(node for node in self._filterNodes if node.uid == toNodeUid)
        newConnection = Connection(fromNode, fromEffectChannel, toNode, toEffectChannel)
        newConnection.uid = uuid.uuid4().hex
        self._filterConnections.append(newConnection)
        toNode._incomingConnections.append(newConnection)
        self._updateProcessOrder()
        return newConnection
    
    def removeConnection(self, fromEffect, fromEffectChannel, toEffect, toEffectChannel):
        """Removes a connection between two filters
        """
        # find connection
        con = next(con for con in self._filterConnections if con.fromNode.effect == fromEffect and con.toNode.effect == toEffect and con.fromChannel == fromEffectChannel and con.toChannel == toEffectChannel)
        if con != None:
            self._filterConnections.remove(con)
            con.toNode._incomingConnections.remove(con)
        None
    
    def _updateProcessOrder(self):
        # reset
        self._processOrder = []
        # find nodes without inputs
        allNodes = self._filterNodes.copy()
        for con in self._filterConnections:
            if allNodes.count(con.toNode) > 0:
                allNodes.remove(con.toNode)
        
        # Add those nodes first
        for node in allNodes:
            self._processOrder.append(node)
        
        #print("{} of {} nodes without inputs processed".format(len(self._processOrder), len(self._filterNodes)))

        # Process others
        connectionsToProcess = self._filterConnections.copy()
        while len(connectionsToProcess) > 0:
            nodesBefore = len(self._processOrder)
            # find nodes with connections only relying on nodes already in chain
            candidates = self._filterNodes.copy()
            for node in self._processOrder:
                candidates.remove(node)
            
            # if we find a connection with anything other than input nodes already processed, those are not candidates

            for con in connectionsToProcess:
                if self._processOrder.count(con.fromNode) <= 0 and candidates.count(con.toNode) > 0:
                    candidates.remove(con.toNode)
            
            # append all candidates
            for node in candidates:
                self._processOrder.append(node)
            
            # update connections to process
            for con in connectionsToProcess.copy():
                if self._processOrder.count(con.fromNode) > 0 and self._processOrder.count(con.toNode) > 0:
                    connectionsToProcess.remove(con)

            #print("{} of {} nodes processed".format(len(self._processOrder), len(self._filterNodes)))

            if len(self._processOrder) == nodesBefore:
                print("circular graph detected")
                raise RuntimeError("circular graph detected")

        if len(self._processOrder) != len(self._filterNodes):
            raise RuntimeError("not all nodes processed")

    def __getstate__(self):
        state = {}
        nodes = [node for node in self._filterNodes]
        state['nodes'] = nodes
        connections = []
        for con in self._filterConnections:
            connections.append(con.__getstate__())
        state['connections'] = connections
        state['recordTimings'] = self.recordTimings
        return state

    def __setstate__(self, state):
        self.__init__()
        self.recordTimings = state['recordTimings']
        nodes = state['nodes']
        for node in nodes:
            newnode = self.addEffectNode(node.effect)
            newnode.uid = node.uid
        connections = state['connections']
        for con in connections:
            fromChannel = con['from_node_channel']
            toChannel = con['to_node_channel']
            self.addNodeConnection(con['from_node_uid'], fromChannel, con['to_node_uid'], toChannel)
        
        
