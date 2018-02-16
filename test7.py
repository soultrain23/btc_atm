import threading
from datetime import datetime
import time

for a in range(100):
    time.sleep(1)
    print(datetime.now())