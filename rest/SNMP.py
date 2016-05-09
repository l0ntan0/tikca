import socket
import sys
import re
import datetime
import logging
import time
import os

from pysnmp.entity.rfc3413.oneliner import cmdgen

# needed packages under Ubuntu 14.04: python3-pysnmp4, python3-regex


# TODO:
# - Aufnahmefunktionalität (höhö)
# - Drauf achten, dass Befehle nur von der entsprechenden IP kommen dürfen
# - Threading/Forking/Multiprocessing, was auch immer
# - SNMP ohne Syscall
# - PipelineStart/Stop/Pause müssen Exceptions werfen (die auch sinnvoller sind als ValueError)
##########################################

# IP and UDP port the Capture Agent should listen to
RcvHost = ''   
RcvPort = 8888 

# IP and UDP port the control system (Crestron etc.) is listening on
CtrlHost = '127.0.0.1'
CtrlPort = 8889

# network controllable outlet (Gude Expert Power Control 8210)
	# Test
NetOutletGetState = '1.3.6.1.4.1.28507.29.1.3.1.2.1.3.4'	# Test
 
logfile = 'ca_log.txt'
logging.basicConfig(filename=logfile, level=logging.DEBUG)

#################################################################################
from snmp_helper import snmp_get_oid,snmp_extract
COMMUNITY_STRING = '<community>'
SNMP_PORT = 161
a_device = ('172.25.107.11', COMMUNITY_STRING, SNMP_PORT)
  

def setOnAirLight(onairstate):
  # cmdGen.getCmd(cmdgen.CommunityData('private'), cmdgen.UdpTransportTarget((NetOutletHost, 161)), cmdgen.MibVariable
  #Todo, das funktioniert aus Python noch nicht richtig. Wir machens mit nem Syscall:
  if onairstate == True:
    newstat = "1"
  else: 
    newstat = "0"
  cmd = "snmpset -v2c -mALL -c private %s %s integer %s" % (NetOutletHost, NetOutletSetState, newstat)
  logging.debug("SNMP-Command: " + cmd)
  os.system(cmd)

if __name__ == '__main__':
  #setOnAirLight(False)
  logging.debug("Started TIKCA " + str(datetime.datetime.now()))
    



####

cmdGen = cmdgen.CommandGenerator()

errorIndication, errorStatus, errorIndex, varBinds = cmdGen.setCmd(
    cmdgen.CommunityData('private'),
    cmdgen.UdpTransportTarget((NetOutletHost, 161)),
    (cmdgen.MibVariable('SNMPv2-MIB', 'iso.3.6.1.4.1.28507.29.1.3.1.2.1.3.4', 1), '1')
)

# Check for errors and print out results
if errorIndication:
    print(errorIndication)
else:
    if errorStatus:
        print('%s at %s' % (
            errorStatus.prettyPrint(),
            errorIndex and varBinds[int(errorIndex)-1] or '?'
            )
        )   
    else:
        for name, val in varBinds:
            print('%s = %s' % (name.prettyPrint(), val.prettyPrint()))     


  
  
  
