from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
import time

class MyListener(ServiceListener):
    def __init__(self):
        self.found = []
    def update_service(self, zc, type_, name):
        pass
    def remove_service(self, zc, type_, name):
        pass
    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        print(f"Service {name} added, service info: {info}")
        self.found.append(name)

zeroconf = Zeroconf()
listener = MyListener()
browser = ServiceBrowser(zeroconf, "_reachy._tcp.local.", listener)
time.sleep(5)
zeroconf.close()

if not listener.found:
    print("No Reachy robots found on the network.")
else:
    print(f"Found robots: {listener.found}")
