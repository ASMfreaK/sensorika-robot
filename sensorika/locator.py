"""DNS-like service to track and perform multiple
updates on workers"""
import datetime

try:
    import ujson as json
except ImportError:
    #print('ujson not found, using json')
    import json
import logging
import signal
import threading
import time

import plyvel
import zmq

from .connector import ConnectorAsync
from .tools import getLocalIp

PORT = 15701

"""
class ThreadedConnector(threading.Thread):
    def __init__(self, ip, port, params=None, database=None, *args, **kwargs):
        threading.Thread.__init__(self, args=args, kwargs=args)
        try:
            if params:
                self.params = params
            else:
                import random
                self.params = dict(frequency=1.0, name='none_{0}'.format(random.randint(0, 1000)))
            self.Estop = threading.Event()
            self.connector = Connector(ip, port)
            self.data = []

            self.db = database
            self.name = self.params['name']
            self.db.statSession(self.name)
            self.thread = self.start()
        except Exception as e:
            print(e)

    def run(self):
        while not self.Estop.is_set():
            t0 = time.time()

            data = self.connector.get()
            t, d = data
            # do not store equal data
            canAdd = False
            if self.data:
                if self.data[-1][0] != t:
                    canAdd = True
            else:
                canAdd = True
            if canAdd:
                if isinstance(t, float):
                    self.data.append(([time.time()] + [t], d))
                else:
                    self.data.append(([time.time()] + t, d))
                self.db.add(self.name, self.data[-1])
            if len(self.data) > 100:
                self.data = self.data[-100:]

            dt = time.time() - t0
            time.sleep(1 / self.params['frequency'])

    def stop(self):
        self.Estop.set()
"""


class ConnectorAsyncDB(object):
    def __init__(self, ip, port, params, database):
        self.db = database
        self.params = params
        self.data = []
        self.db.statSession(params['name'])

        self.connector = ConnectorAsync(ip, port, callback=self.add)

    def add(self, data):
        t, d = data
        canAdd = False
        if self.data:
            if self.data[-1][0] != t:
                canAdd = True
        else:
            canAdd = True
        if canAdd:
            if isinstance(t, float):
                self.data.append(([time.time()] + [t], d))
            else:
                self.data.append(([time.time()] + t, d))

            self.db.add(self.name, self.data[-1])
            if len(self.data) > 100:
                self.data = self.data[-100:]

    def stop(self):
        self.connector.stop()


class DatabaserLEVELDB():
    def __init__(self):
        self.db = plyvel.DB('./db', create_if_missing=True)

    def close(self):
        if not self.db.closed:
            self.db.close()

    def statSession(self, name):
        self.db.put("session|{1}|{0}".format(name, time.time()).encode('utf8'), b'ok')

    def add(self, name, data):
        if not self.db.closed:
            self.db.put(
                "{0}-{1}".format(name, time.time()).encode('utf8'),
                json.dumps(data).encode("utf8")
            )

    def getSessions(self, datefrom=None, dateto=None):
        df = b"0"
        dt = str(time.mktime(datetime.datetime.now().timetuple())).encode("utf8")
        if datefrom:
            df = str(time.mktime(datefrom.timetuple())).encode("utf8")
        if dateto:
            dt = str(time.mktime(dateto.timetuple())).encode("utf8")
        d = []
        for k, v in self.db.iterator(start=b'session|' + df, stop=b'session|' + dt, reverse=True):
            d.append(k.decode('utf8').split('|')[1:])
            d[-1][0] = str(datetime.datetime.fromtimestamp(float(d[-1][0])))
        return d

    def getdata(self, name, datefrom=None, dateto=None, limit=1000):

        df = b"0"
        dt = str(time.mktime(datetime.datetime.now().timetuple())).encode("utf8")
        if datefrom:
            df = str(time.mktime(datefrom.timetuple())).encode("utf8")
        if dateto:
            dt = str(time.mktime(dateto.timetuple())).encode("utf8")

        d = []
        l = 0
        for k, v in self.db.iterator(start=name.encode('utf8') + b"-" + df,
                                     stop=name.encode('utf8') + b"-" + dt, reverse=True):
            dbtime = float(k.decode('utf8').split("-")[1])
            # d.append([k.decode('utf8'), v.decode('utf8')])
            data = json.loads(v.decode('utf8'))
            # d[-1][0]=float(d[-1][0].split("-")[1])
            # d[-1][1]=json.loads(d[-1][1])
            d.append(data)
            d[-1][0] = [dbtime] + d[-1][0]
            l += 1
            if l >= limit:
                break

        return d


class Locator(threading.Thread):
    def __init__(self, *a, **k):
        threading.Thread.__init__(self, args=a, kwargs=k)
        self.programs = dict()
        self.EStop = threading.Event()
        self.db = DatabaserLEVELDB()

    def stop(self, *p1, **p2):
        for k, v in self.programs.items():
            v['con'].stop()
            self.EStop.set()
        self.db.close()
        print("STOP!")

    def run(self):

        logging.debug("Starting nameserver on {0}:{1}".format(getLocalIp(), str(PORT)))
        time.sleep(1)
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        try:
            socket.bind("tcp://*:" + str(PORT))
        except:
            self.EStop.set()
            logging.error("port busy")
        self.programs = {}
        counter = 0
        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)
        while not self.EStop.is_set():
            data = {}
            logging.debug('tick')

            try:
                socks = dict(poller.poll(1000))
            except:
                pass
            if socket in socks:
                try:
                    data = socket.recv_json()
                except Exception as e:
                    print(e)
                    continue
            else:
                continue

            logging.debug('Recive')
            try:
                data['action']
            except:
                data['action'] = 'ping'
            try:
                answer = None
                if data['action'] == 'register':

                    try:
                        data['location']
                    except:
                        data['location'] = 'local'
                    if data['name'] in self.programs.keys():
                        if self.programs[data['name']]['port'] != data['port']:
                            self.programs[data['name']]['params'] = data['params']
                            self.programs[data['name']]['con'].stop()
                            self.programs[data['name']]['con'] = ConnectorAsyncDB(
                                data['ip'], data['async_port'],
                                data['params'], self.db)
                    else:
                        self.programs[data['name']] = dict(time=time.time(), params=data['params'])
                        self.programs[data['name']]['con'] = ConnectorAsyncDB(
                            data['ip'], data['async_port'],
                            data['params'], self.db)
                    answer = dict(status='ok')
                if data['action'] == 'list':
                    d = []
                    for k, v in self.programs.items():
                        d.append(dict(name=k, data=v['params']))  # TODO: Fix, couse` ThreadedConnector is not json
                    answer = d
                if data['action'] == 'listsessions':
                    df = None
                    dt = None
                    if 'df' in data.keys():
                        df = data['df']
                    if 'dt' in data.keys():
                        dt = data['dt']
                    answer = self.db.getSessions(datefrom=df, dateto=dt)

                if data['action'] == 'getdata':
                    df = None
                    dt = None
                    limit = 10
                    if 'df' in data.keys():
                        df = data['df']
                    if 'dt' in data.keys():
                        dt = data['dt']
                    if 'limit' in data.keys():
                        limit = data['limit']
                    answer = self.db.getdata(name=data['name'], datefrom=df, dateto=dt, limit=limit)

                if data['action'] == 'get':
                    if 'count' not in data.keys():
                        data['count'] = 1
                    if 'name' not in data.keys():
                        # get all data
                        tmp = dict()
                        for k, v in self.programs.items():
                            tmp[k] = v['con'].data[-data['count']:]
                        print(tmp, self.programs)
                        answer = dict(status='ok', data=tmp)
                    else:
                        d = self.programs[data['name']]['con'].data[-data['count']:]
                        answer = dict(status='ok', data=d)

                if data['action'] == 'ping':
                    answer = dict(status='ok')

            except Exception as e:
                answer = dict(status='fail', text=str(e))

            if answer:
                socket.send_json(answer)
            else:
                socket.send_json(dict(status='fail', text='wrong action'))

        socket.close()
        context.term()

        time.sleep(0.01)
        logging.debug("closing")


def serve():
    l = Locator()
    signal.signal(signal.SIGINT, l.stop)
    signal.signal(signal.SIGTERM, l.stop)
    print('Serving at {0}'.format(PORT))
    l.start()


if __name__ == "__main__":
    serve()
