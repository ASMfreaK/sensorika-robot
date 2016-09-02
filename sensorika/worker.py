"""`Server` part of the library"""
try:
    import ujson as json
except ImportError:
    #print('ujson not found, using json')
    import json

import logging
import threading
import time

import zmq

from sensorika.tools import getLocalIp


class Worker(threading.Thread):
    def __init__(self, name, configFile=None, *args, **kwargs):
        self.Estop = threading.Event()
        threading.Thread.__init__(self, args=args, kwargs=args)
        self.src = ""
        self.command = []
        time.sleep(0.1)
        self.name = name
        self.dt = 0.001
        self.ns_ip = getLocalIp()
        self.name = name
        self.canGo = True
        self.lastSSend = time.time()
        self.data = [(time.time(), 0)]
        self.wcontext = zmq.Context()
        self.sync_socket = self.wcontext.socket(zmq.REP)
        self.async_socket = self.wcontext.socket(zmq.PUB)
        self._configFile = "." + name
        self.ptimer = threading.Timer(2.0, self.populate)

        if configFile:
            self._configFile = configFile
        need_create = True
        try:
            f = open(self._configFile, "r")
        except IOError as e:
            print('Creating config File with random port')
            self.sync_port = self.sync_socket.bind_to_random_port("tcp://*")
            self.async_port = self.sync_socket.bind_to_random_port("tcp://*")
            self.params = {}
            self.params['port'] = self.sync_port
            self.params['sync_port'] = self.sync_port
            self.params['async_port'] = self.async_port
            self.params['frequency'] = 10
            self.params['name'] = name
            f = open(self._configFile, "w")
            f.write(json.dumps(self.params))
            f.close()
            need_create = False
        if need_create:
            try:
                self.params = json.load(open(self._configFile, "r"))
                self.sync_socket.bind("tcp://*:{0}".format(self.params['port']))
                self.async_socket.bind("tcp://*:{0}".format(self.params['async_port']))
            except Exception as e:
                print(e)
                return

        self.ptimer.start()
        print("Serving at sync {0} and async {1}".format(self.params['sync_port'], self.params['async_port']))
        self.start()

    def populate(self):
        logging.debug('populating')
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REQ)
        s = "tcp://" + self.ns_ip + ":15701"

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN | zmq.POLLOUT)
        sock.connect(s)
        if poller.poll(3 * 1000):  # 10s timeout in milliseconds
            sock.send_json(dict(action='register', name=self.name, port=self.params['port'],
                                ip=self.ns_ip, params=self.params))
        else:
            logging.error("No locator on {0}:{1}".format(self.ns_ip, 15701))

        if poller.poll(3 * 1000):  # 10s timeout in milliseconds
            sock.recv_json()
        else:
            logging.error("No locator on {0}:{1}".format(self.ns_ip, 15701))

        sock.close()
        ctx.term()
        if not self.Estop.is_set():
            self.ptimer = threading.Timer(10.0, self.populate)
            self.ptimer.start()

    def add(self, data):
        self.data.append((time.time(), data))
        self.async_socket.send_json(self.data[-1])
        if len(self.data) > 100:
            self.data = self.data[-100:]

        return data

    def get(self, cnt=1):
        try:
            return self.command[-cnt:]
        except:
            return None

    def run(self):
        self.canGo = True
        self.command = []
        cnt = 0

        try:
            while True:
                if self.Estop.is_set():
                    break

                try:
                    data = self.sync_socket.recv(zmq.DONTWAIT).decode("utf8")
                except:
                    time.sleep(0.001)
                    continue

                data = json.loads(data)
                senddata = None
                try:
                    if data['action'] == 'call':
                        pass
                    if data['action'] == 'source':
                        if self.src:
                            senddata = dict(source=self.src)
                    if data['action'] == 'line':
                        if self.src:
                            senddata = dict(line=self.src_line)

                    if data['action'] == 'get':
                        senddata = self.data[-1]
                    if data['action'] == 'set':
                        self.command.append((time.time(), data['data']))
                        if len(self.command) > 100:
                            self.command = self.command[-100:]
                        senddata = dict(status='ok')
                except Exception as e:
                    print(e)
                    sneddata = None
                    self.sync_socket.send_json(dict(status='wrong params'))
                try:
                    self.sync_socket.send_json(senddata)
                except Exception as e:
                    print(e)

                time.sleep(self.dt)

            self.sync_socket.close()
            self.wcontext.term()
        except Exception as e:
            print(e)
            self.sync_socket.send_json(dict(status='error', error=str(e)))

    def stop(self):
        self.sync_socket.setsockopt(zmq.LINGER, 0)
        self.async_socket.setsockopt(zmq.LINGER, 0)

        self.Estop.set()
        self.ptimer.cancel()
        self.sync_socket.close()
        self.async_socket.close()

def mkPeriodicWorker(name, function, params={}, configFile=None):
    w = Worker(name, configFile)
    w.params.update(params)
    spd = 1.0 / w.params['frequency']

    def W():
        while not w.Estop.is_set():
            t0 = time.time()
            result = function()
            w.add(result)
            tt = time.time() - t0
            time.sleep(spd - tt)
    t = threading.Thread(target=W)
    t.start()
    return w
