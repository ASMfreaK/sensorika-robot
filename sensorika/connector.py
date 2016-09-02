"""`Client` part of library"""
import datetime
import threading
import time

import zmq


class Connector:
    def __init__(self, ip, port=15701, name=""):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.addr = "tcp://" + ip + ":" + str(port)
        self.socket.connect(self.addr)
        self.t0 = time.time() - 10000
        self.dataReady = False
        t = str(datetime.datetime.now())
        self.cache = None

        self.Estop = threading.Event()

    def get(self, dt=0.05):
        if (self.t0 + dt > time.time()) and (self.dataReady):
            return self.cache
        else:
            self.socket.send_json(dict(action='get', count=1))
            self.cache = self.socket.recv_json()
            self.t0 = time.time()
            self.dataReady = True
            return self.cache

    def set(self, data=None):
        data = dict(action='set', data=data, count=1)
        self.socket.send_json(data)
        return self.socket.recv_json()

    def stop(self):
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.close()


class ConnectorAsync(threading.Thread):
    def __init__(self, ip, port=15701, name="", callback=None):

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.setsockopt(zmq.SUBSCRIBE, b'')

        self.addr = "tcp://" + ip + ":" + str(port)
        self.socket.connect(self.addr)

        self.Estop = threading.Event()
        self.callback = callback

        super(ConnectorAsync, self).__init__()
        self.start()

    def run(self):
        self.cache = [time.time(), None]
        poller = zmq.Poller()
        poller.register(self.socket, zmq.POLLIN)
        while not self.Estop.is_set():
            try:
                socks = dict(poller.poll(1000))
            except KeyboardInterrupt:
                break
            if self.socket in socks:
                tmp = self.socket.recv_json()
                if isinstance(tmp[0], float):
                    tmp = [[time.time(), tmp[0]], tmp[1]]
                else:
                    tmp = [[time.time()] + tmp[0], tmp[1]]
                self.cache = tmp
                if self.callback:
                    self.callback(self.cache)


    def get(self, dt=0.05):
        return self.cache

    def stop(self):
        self.Estop.set()
        # self.socket.close()
