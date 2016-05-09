#!/bin/bash
cd /home/ruszurek/pyCA_alt_4701
screen -L -S 4701 -d -m /home/ruszurek/pyCA_alt_4701/pyca.py run
cd /home/ruszurek/pyCA_alt_4702
screen -L -S 4702 -d -m /home/ruszurek/pyCA_alt_4702/pyca.py run
cd /home/ruszurek/pyCA_alt_4703
screen -L -S 4703 -d -m /home/ruszurek/pyCA_alt_4703/pyca.py run
