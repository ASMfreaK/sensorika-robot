
import time, datetime, threading
import json, zmq
import  logging
from sensorika.tools import getLocalIp
import json
class Worker(threading.Thread):
    def __init__(self, name, *args, **kwargs):
        self.Estop = threading.Event()
        threading.Thread.__init__(self,args=args, kwargs=args)
        self.src = ""
        self.command = []
        time.sleep(0.1)
        self.name=name
        self.dt = 0.001
        self.ns_ip = getLocalIp()
        self.name = name
        self.canGo = True
        self.lastSSend = time.time()
        self.data = [(time.time(), 0)]
        self.wcontext = zmq.Context()
        self.wsocket = self.wcontext.socket(zmq.REP)
        try:
            f = open('{0}.state'.format(self.name), "r")
            txt = f.readline()
            self.port = int(txt)
            self.wsocket.bind("tcp://*:{0}".format(self.port))
        except Exception as e:
            print(e)
            self.port = self.wsocket.bind_to_random_port("tcp://*")
            f = open('{0}.state'.format(self.name), "w")
            f.write(str(self.port))
            f.close()
        self.ptimer = threading.Timer(10.0, self.populate)
        self.ptimer.start()
        print("Serving at {0}".format(self.port))
        self.start()
    def populate(self):
        logging.debug('populating')
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REQ)
        s = "tcp://" + self.ns_ip + ":" + str(self.port)

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        if poller.poll(5 * 1000):  # 10s timeout in milliseconds
            sock.connect(s)
            sock.send_json(dict(action='register', name=self.name, port=self.port, ip=self.ns_ip))
            z = sock.recv_json()
        else:
            logging.error("No locator on {0}:{1}".format(self.ns_ip, 15701))

        sock.close()
        ctx.term()
        if not self.Estop.is_set():
            self.ptimer = threading .Timer(10.0, self.populate)
            self.ptimer.start()

    def add(self, data):
        self.data.append((time.time(), data))
        self.command.append((time.time(), data))
        if len(self.data) > 100:
            self.data = self.data[-100:]
        return data

    def get(self, cnt=1):
        import types
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
                if self.Estop .is_set():
                    break

                try:
                    data = self.wsocket.recv(zmq.DONTWAIT).decode("utf8")
                except:
                    time.sleep(0.001)
                    continue

                data = json.loads(data)
                try:
                    if data['action'] == 'call':
                        pass
                    if data['action'] == 'source':
                        if self.src:
                            self.wsocket.send_json(dict(source=self.src))
                    if data['action'] == 'line':
                        if self.src:
                            self.wsocket.send_json(dict(line=self.src_line))

                    if data['action'] == 'get':
                        self.wsocket.send_json(self.data[-1])
                    if data['action'] == 'set':
                        self.command.append((time.time(), data['data']))
                        self.wsocket.send_json(dict(status='ok'))
                except Exception as e:
                    print(e)
                    self.wsocket.send_json(dict(status='wrong params'))
                time.sleep(self.dt)
            self.wsocket.close()
            self.wcontext.term()
        except Exception as e:
            print(e)
            self.wsocket.send_json(dict(status='error', error=str(e)))

    def stop(self):
        self.wsocket.setsockopt(zmq.LINGER, 0)
        self.Estop.set()
        self.ptimer.cancel()

def mkPeriodicWorker(name, function, params={}):
    w = Worker(name)
    def W():
        while not w.Estop.is_set():
            result = function()
            w.add(result)
            time.sleep(1)
            print(result)
    t = threading.Thread(target=W)
    t.start()
    return w