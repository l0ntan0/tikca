#!/usr/bin/env python3
import threading
from queue import Queue
import datetime
import time
import socket
import socketserver
import os, sys
import subprocess, shlex
import csv
import base64
from gi.repository import GLib
from configobj import ConfigObj
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
from io import BytesIO as bio
import pycurl
import json

os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

TIKCFG = ConfigObj('/etc/tikca.conf', list_values=True)
globals()['TIKCFG'] = TIKCFG
from grabber import Grabber

from pyca import ca
CONFIG = ca.CONFIG

#3801: \r\n am Ende von befehlen vom ctrlstation
#4701: \n am Ende von Befehlen von Ctrlstation
#4701: \n beim Senden zu Ctrlstation




# lock to serialize console output
#lock = threading.Lock()
# todo:
# pause
# subdir_nextrecording with sense
# in "grabber" noch mal alle änderungen mit logging machen. grrrrr.



import logging
# basic config is done in ca.py
# here, we're adding a file handler
console = logging.FileHandler(TIKCFG['logging']['fn'], mode='a', encoding="UTF-8")
console.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s %(levelname)-8s '
                              + '[%(filename)s:%(lineno)s:%(funcName)s()] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
# tell the handler to use this format
console.setFormatter(formatter)
# add the handler to the root logger
logging.getLogger('').addHandler(console)


#subdir_nextrecording = "aaaa" # why is that here

class TIKCAcontrol():
    global TIKCFG
    def __init__(self):
        logging.info("STARTING CONTROL LOOPS...")
        # for episode management
        self.ANNOUNCEMENT = "No upcoming events."
        self.NEXTEPISODE = None
        self.NEXTPROPS = None
        self.NEXTEVENT = None
        self.NEXTSUBDIR = None
        self.CURSUBDIR = None

        self.get_current_recording_data()
        self.tries = 0
        self.ultimatetries = 0

        # for udp server
        self.dest = (TIKCFG['udp']['ctrlstation'], int(TIKCFG['udp']['sendport']))
        self.sendsocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sendsocket.connect(self.dest)
        self.block_cmd = False
        self.udpconnectloop()

        #todo: Watchdog für pausierte Recordings - falls seit mehr als n sekunden pausiert, stop und ingest

    def curlreq(self, url, post_data=None):

        buf = bio()
        curl = pycurl.Curl()
        curl.setopt(curl.URL, url.encode('ascii', 'ignore'))

        if post_data:
            curl.setopt(curl.HTTPPOST, post_data)

        curl.setopt(curl.WRITEFUNCTION, buf.write)
        curl.perform()
        status = curl.getinfo(pycurl.HTTP_CODE)
        curl.close()
        if int(status / 100) != 2:
            raise Exception('ERROR: Request to %s failed (HTTP status code %i)' % \
                            (url, status))
        result = buf.getvalue()

        buf.close()
        return result

    def udprc_setup(self):
        try:
            self.udpserver = socketserver.UDPServer(server_address=(TIKCFG['udp']['listenhost'],
                                                                    int(TIKCFG['udp']['listenport'])),
                                                    RequestHandlerClass=UDPHandler)
            return True
        except OSError:
            logging.error("Could not start UDP server!")
            return False

    def udpconnectloop(self):
        try:
            self.sendsocket.connect(self.dest)
        except:
            logging.error("Cannot connect to UDP CtrlStation (%s:%s)"%
                          (TIKCFG['udp']['ctrlstation'], TIKCFG['udp']['sendport']))

    def get_outlet_state(self, n):
        if n in range(0, len(TIKCFG['outlet']['hosts'])):
            if len(TIKCFG['outlet']['users'][n]) > 0:
                url = "http://%s:%s@%s/statusjsn.js?components=1073741823"%(
                    TIKCFG['outlet']['users'][n], TIKCFG['outlet']['passwords'][n], TIKCFG['outlet']['hosts'][n])
            else:
                url = "http://%s/statusjsn.js?components=1073741823" % (
                    TIKCFG['outlet']['hosts'][n])
            #try:
            jsonstring = self.curlreq(url).decode("UTF-8")
            jsondata = json.loads(jsonstring)
            # outlet nr counting from the config file starts with 1,
            # the json array stars with 0, so we have to compensate:
            jsonoutletnr = int(TIKCFG['outlet']['outletnrs'][n])-1
            return(jsondata['outputs'][jsonoutletnr]['state'],
                   jsondata['outputs'][jsonoutletnr]['name'])
            #except:
            #    logging.error("Error reading outlet data under %s"%url)
            #    return(0)



    def set_outlet_state(self, n, state):
        if state in ["0", "1"]:
            if n in range(0, len(TIKCFG['outlet']['hosts'])):
                # if there is a user and password set, use them
                if len(TIKCFG['outlet']['users'][n]) > 0:
                    url = "http://%s:%s@%s/ov.html?cmd=1&p=%s&s=%s"%(
                        TIKCFG['outlet']['users'][n], TIKCFG['outlet']['passwords'][n], TIKCFG['outlet']['hosts'][n],
                        TIKCFG['outlet']['outletnrs'][n], state
                    )
                else:
                # we do not need username and password
                    url = "http://%s/ov.html?cmd=1&p=%s&s=%s" % (
                    TIKCFG['outlet']['hosts'][n], int(TIKCFG['outlet']['outletnrs'][n]), state
                )

            logging.debug("Setting outlet %s to state %s"%(n, state))
            print(url)
            self.curlreq(url)

        else:
            logging.error("Cannot set weird state %s for outlet."%state)
        pass

    def watch_lights(self):
        # sets recording/camera light in expected intervals
        global grabber, mycontrol
        if grabber.get_recstatus() == "RECORDING":
            newstate = "1"
        else:
            newstate = "0"

        while True:
            for n in range (0, len(TIKCFG['outlet']['hosts'])):
                if len(TIKCFG['outlet']['hosts'][n]) > 2:
                    (curstate, outletname) = self.get_outlet_state(n)
                    logging.debug("Current state of outlet %s ('%s'): %s. Should be: %s."%(n, outletname, curstate, newstate))
                    if not int(curstate) == int(newstate):
                        self.set_outlet_state(n, newstate)
            time.sleep(float(TIKCFG['outlet']['update_frequency']))

                # http://wiki.gude.info/EPC_HTTP_Interface#Output_switching_.28easy_switching_command.29


    def get_current_recording_data(self):
        # asks server for upcoming recording
        # updates announcement
        # updates "NEXT..." variables
        global grabber, mycontrol
        logging.info("Status update: %s"%grabber.get_ocstatus())
        ca.register_ca(status=grabber.get_ocstatus())

        t = threading.Timer(CONFIG['agent']['update_frequency'], self.get_current_recording_data)
        t.start()
        # todo speicherverbrauch?

        tmp = ca.get_schedule()
        if tmp == None or len(tmp) == 0:
            logging.info("No upcoming events.")
            self.ANNOUNCEMENT = "No upcoming events."
            self.NEXTEPISODE = None
            self.NEXTPROPS = None
            self.NEXTEVENT = None
            self.NEXTSUBDIR = None
            return (None, None)

        nextevent = tmp[0] # in tmp, there is a list of events. [0] is the upcoming one.

        delta = nextevent[0] - time.time()
        d_start = datetime.datetime.fromtimestamp(nextevent[0]).strftime('%Y-%m-%d, %H:%M')
        d_end = datetime.datetime.fromtimestamp(nextevent[1]).strftime('%H:%M')
        d_timeuntil = datetime.timedelta(seconds=delta)
        uid = nextevent[3].get("uid")

        if delta < 60*3: # RIGHT NOW (or at least in three minutes), there is an event in this room!
            self.ANNOUNCEMENT = "Current event in this room: '%s' (ends at %s)"%\
                                (nextevent[3].get("SUMMARY"), d_end)
            logging.debug(self.ANNOUNCEMENT)
            self.NEXTSUBDIR = datetime.datetime.fromtimestamp(nextevent[0]).strftime('%Y%m%d') + "_" + uid
            if not grabber.get_recstatus() in ("RECORDING", "PAUSED", "STARTING", "PAUSING"):
                self.CURSUBDIR = self.NEXTSUBDIR
            # We need two variables for that. Because: Image recording A is scheduled from 10:00 till 10:30,
            # recording B from 10:31 to 11:00. When A takes until 10:35, stuff gets written into B's directory from 10:30 on.
            # So we make sure that NEXTSUBDIR gets overwritten when there's another recording, CURSUBDIR only gets overwritten
            # when there's no recording going on *right now*.

            self.NEXTEVENT = nextevent

            # if a) automatic recordings are True,
            # b) there is nothing being recorded right now and we're not starting the recording right now,
            # c) there is a scheduled recording for this CA: start recording!
            if (grabber.get_recstatus() == "IDLE" or grabber.get_recstatus() == "ERROR")\
                and TIKCFG['capture']['autostart'] == True \
                and not grabber.get_recstatus() == "STARTING":

                logging.debug("Trying to start recording because event is happening and autostart is set to True...")
                grabber.setup_recorddir(subdir=mycontrol.NEXTSUBDIR, epidata=mycontrol.NEXTEPISODE)
                self.tries = 0
                if grabber.standby():
                    logging.debug("Grabber is in standby mode. Starting recording...")
                    grabber.record_start()
                    # from here on, leave it to watch_recstart to start the recording
                    self.t5 = threading.Timer(2.0, mycontrol.watch_recstart)
                    self.t5.start()

                else:
                    logging.error("Grabber failed to enter standby mode before recording!")

        else:
            # currently, there is no event. Things to do:
            # 1. Set up announcement for the next event and
            # 2. set everything ready for unscheduled recordings
            # 3. stop automatic recordings, if there are any running right now
            if grabber.get_recstatus() == "RECORDING" and TIKCFG['capture']['autostart'] == True:
                logging.debug("Stopping autostarted recording...")
                grabber.record_stop()
                self.ingest(CONFIG['capture']['directory'] + "/" + self.LASTSUBDIR)

            logging.debug("Next event ('%s', UID %s) occurs from %s to %s. This is in %s."%
                          (nextevent[3].get("SUMMARY"), uid, d_start, d_end, d_timeuntil))
            self.ANNOUNCEMENT = "Next event: '%s' at %s."%\
                            (nextevent[3].get("SUMMARY"), d_start)

            # and, if there was a recording going on because of autostart, stop it
            # TODO FETTES TODO: AUTOSTOP FUNZT NICHT!!!!!!!!!!!!!! (da fehlt ja auch ne zeitliche bedingung)



        self.NEXTEPISODE = base64.b64decode(str(nextevent[3].get("attach")[0])).decode("utf-8")  # episode.xml
        self.NEXTPROPS = base64.b64decode(str(nextevent[3].get("attach")[1])).decode("utf-8")
        grabber.NEXTWFIID = uid

        return (self.NEXTEPISODE, self.NEXTPROPS)


    def sendtoctrlstation(self, data):
        try:
            #logging.debug("Sending UDP message: '%s'"%data)
            self.sendsocket.sendall(data.encode("utf-8"))
        except ConnectionRefusedError:
            logging.error("Cannot send UDP message %s to %s:%s"%(data, TIKCFG['udp']['ctrlstation'],
                                                                int(TIKCFG['udp']['sendport'])))


    def watch_recstart(self):
        global grabber
        GObject.threads_init()
        Gst.init(None)
        # We're only called when a record should be starting.
        # Therefore, if the pipeline is in Gst.State.PAUSED, we're not running while we should be.
        if grabber.pipeline.get_state(False)[1] == Gst.State.PAUSED and self.ultimatetries <= 5:
            # try again to push the PLAY-Button
            grabber.pipeline.set_state(Gst.State.PLAYING)
            t = threading.Timer(1.0, self.watch_recstart)
            t.start()
            # ... and watch over it in one second again
            self.tries += 1
            # try three times to start by setting "Gst.State.PLAYING"

            if self.tries > 2:
                # if that doesn't help, kill the pipeline and start all over again
                logging.error("Waited 3 seconds to start pipeline")
                grabber.record_stop(eos=False)
                logging.error("Pipeline didn't start - stopped and removed. Restarting.")
                grabber.standby()
                grabber.set_recstatus("STARTING")
                # push "record start" after one second, look after it after 3 seconds
                t3 = threading.Timer(0.5, grabber.record_start)
                t3.start()
                t4 = threading.Timer(3.0, self.watch_recstart)
                t4.start()

                self.tries = 0
                self.ultimatetries += 1


        elif grabber.pipeline.get_state(False)[1] == Gst.State.PLAYING:
            # we're finally happy: The pipeline is running
            logging.info("Pipeline running!")
            grabber.set_recstatus("RECORDING")
            grabber.set_ocstatus("capturing")

        if self.ultimatetries > 5:
            # we tried five times to remove and rebuild the pipeline; this has to end.
            logging.error("FATAL ERROR! Tried five times to remove and rebuild pipeline. Not trying anymore.")
            grabber.set_recstatus("IDLE")
            # todo: set state to error
            self.ultimatetries = 0



    def watch_length(self):
        # stop recording when maximum length is reached
        #todo hat noch nicht funktioniert
        if grabber.get_recstatus() == "RECORDING" and grabber.recordingsince != None:
            ts_begin = time.mktime(grabber.recordingsince.timetuple())
            ts_now = time.mktime(datetime.datetime.now().timetuple())
            logging.debug("Recording since %s min (max: %s min)."%
                          (round((ts_now-ts_begin)/60), TIKCFG['capture']['maxduration']))

            if ts_now-ts_begin > float(TIKCFG['capture']['maxduration'])*60:
                grabber.record_stop()
                ingest_thread = threading.Thread(target=self.ingest, args=(grabber.RECDIR,))
                ingest_thread.start()
                logging.error("Stopping the recording since maximum duration of %s min has been reached."
                              %TIKCFG['capture']['maxduration'])

        self.tp = threading.Timer(20.0, self.watch_length)
        self.tp.start()

    def hostcheck(self, host):
        status, result = subprocess.getstatusoutput("ping -c2 -w2 " + str(host))
        if status == 0:
            logging.debug("Host %s is up!"%str(host))
            return True
        else:
            logging.debug("Host %s is down!"%str(host))
            return False

    def watch_hosts(self):
        # check hosts via ping whether they are up
        # if we are recording, we suppose that the hosts in question are up, so we're not testing
        if grabber.get_recstatus() != "RECORDING":
            if len(TIKCFG['capture']['src1_adminhost']) > 6:
                # if there's an admin host given, ping it
                logging.debug("Checking whether host %s is up..."%TIKCFG['capture']['src1_adminhost'])
                if not (self.hostcheck(TIKCFG['capture']['src1_adminhost'])):
                    logging.error("Host %s is down!"%TIKCFG['capture']['src1_adminhost'])

            try:
                TIKCFG['capture']['src2_adminhost']
                if len(TIKCFG['capture']['src2_adminhost']) > 6:
                    logging.debug("Checking whether host %s is up..."%TIKCFG['capture']['src2_adminhost'])
                    if not (self.hostcheck(TIKCFG['capture']['src2_adminhost'])):
                        logging.error("Host %s is down!"%TIKCFG['capture']['src2_adminhost'])
            except KeyError:
                pass
        self.tp = threading.Timer(float(TIKCFG['watch']['enc_time']), self.watch_hosts)
        self.tp.start()
    
    def watch_freespace(self):
        if ingester.df() < float(TIKCFG['watch']['df_space']):
            logging.error("WARNING: There are less than %s MB available on recording media."%TIKCFG['watch']['df_space'])
        self.tp = threading.Timer(float(TIKCFG['watch']['df_time']), self.watch_freespace)
        self.tp.start()


class UDPHandler(socketserver.BaseRequestHandler):

    def handle(self):
        global grabber, mycontrol

        data = self.request[0]
        socket = self.request[1]
        data = data.decode('UTF-8')
        logging.debug("UDP message from %s: '%s'"%(format(self.client_address[0]), data.strip()))
        # make sure only one thing is done at a time
        if mycontrol.block_cmd:
            logging.error("Too much for me.")
            return
        # make sure only the control station is allowed to speak with us
        if not self.client_address[0] == TIKCFG['udp']['ctrlstation']:
            logging.error("Not processing UDP message from %s: Origin is not %s."%(self.client_address[0],
                                                                                   TIKCFG['udp']['ctrlstation']))
            return

        if ":RECORD" in data:
            mycontrol.block_cmd = True
            self.tries = 0
            logging.debug("Getting UDP command to start recording")
            # if there is no record going on, setup pipeline and start it
            # if there is a record paused, restart it
            if grabber.get_recstatus() == "IDLE" or grabber.get_recstatus() == "ERROR":
                # try to record
                # is there a scheduled event? -> make recdir according to scheduling
                # but only if its a maximum of five minutes before that event.
                # is there no scheduled event? -> make recdir with unique name
                # is solved in grabber.setup_recorddir
                grabber.setup_recorddir(subdir=mycontrol.NEXTSUBDIR, epidata=mycontrol.NEXTEPISODE)

                logging.debug("Creating pipeline in standby...")
                if grabber.standby():
                    logging.debug("Starting recording.")
                    grabber.record_start()
                    mycontrol.block_cmd = False
                    self.t = threading.Timer(0.8, mycontrol.watch_recstart)
                    self.t.start()
                else:
                    logging.error("Grabber failed to enter standby mode before recording!")

            elif grabber.get_recstatus() == "PAUSED":
                logging.debug("Restarting recording from pause.")
                grabber.record_start()
                self.t = threading.Timer(0.8, mycontrol.watch_recstart)
                self.t.start()
            else:
                logging.error("Got UDP command to START recording while recording is going on already.")
            mycontrol.block_cmd = False

        if ":STOP" in data and (grabber.get_recstatus() == "PAUSED" or grabber.get_recstatus() == "RECORDING"):
            #todo pause -> stop geht irgendwie noch nicht
            mycontrol.block_cmd = True
            logging.debug("Getting UDP command to stop recording")
            if grabber.get_pipestatus() == "capturing":
                logging.info("Stopping recording")
                grabber.record_stop()
                ingester.write_dirstate(mycontrol.CURSUBDIR, "STOPPED")
                mycontrol.CURSUBDIR = None
                #ingest_thread = threading.Thread(target=mycontrol.ingest, args=(grabber.RECDIR,))
                #ingest_thread.start()

            else:
                logging.error("Got UDP command to STOP recording, but no recording is going on.")
            mycontrol.block_cmd = False


        if ":PAUSE" in data:
            mycontrol.block_cmd = True
            logging.debug("Getting UDP command to pause recording")
            if grabber.get_pipestatus() == "capturing":
                grabber.record_pause()
            else:
                logging.error("Got UDP command to PAUSE recording, but no recording is going on.")
            mycontrol.block_cmd = False

        if ":STATE" in data:
            #logging.debug("Received STATE message: %s"%data.strip())
            # write state of Sendeleitung 1 and 2 to file, if recording
            # if not, do nothing
            grabber.write_line_states(data.split(" ")[1])
            # return state to console
            mycontrol.sendtoctrlstation(":%s\n"%grabber.get_recstatus())
            mycontrol.block_cmd = False


ev = threading.Event()

# initialize grabber
grabber = Grabber()
#thread_streamrecorder = threading.Thread(target=grabber.standby)

# initialize control loop/status updater
mycontrol = TIKCAcontrol()



logging.info("STARTING UDP COMMAND SETUP...")
if mycontrol.udprc_setup():
    th = threading.Thread(target=mycontrol.udpserver.serve_forever)
    th.daemon = True
    th.start()
else:
    logging.error("UDP server could not be started.")


if TIKCFG['watch']['enc_check']:
    logging.info("STARTING HOST CHECK...")
    th2 = threading.Thread(target=mycontrol.watch_hosts)
    th2.daemon = True
    th2.start()


logging.info("STARTING RECORDING LENGTH WATCH...")
lw = threading.Thread(target=mycontrol.watch_length)
lw.daemon = True
lw.start()

logging.info("STARTING FREE SPACE WATCHDOG...")
dfw = threading.Thread(target=mycontrol.watch_freespace)
dfw.daemon = True
dfw.start()

if len(TIKCFG['outlet']['hosts'] > 0:
    logging.info("STARTING OUTLET UPDATE LOOP...")
    lw = threading.Thread(target=mycontrol.watch_lights)
    lw.daemon = True
    lw.start()


# to run recording:

def do_testrecording(length=15.0, with_pause=True):
    grabber.setup_recorddir(subdir="Testrecording")
    grabber.standby()
    b = threading.Timer(1.0, grabber.record_start)
    b.start()

    c = threading.Timer(4.0, grabber.get_pipestatus)
    c.start()

    if with_pause:
        d = threading.Timer(1.0 + length/3, grabber.record_pause)
        print("doing a pause at %f"%(length/3))
        d.start()

        e = threading.Timer(1.0 + length/3 + 1.2, grabber.record_start)
        print("restarting from pause at %f"%(length/3 + 1.2))
        e.start()

    a = threading.Timer(1.0+length, grabber.record_stop)
    a.start()


#do_testrecording(20)









#this works:
#server = socketserver.UDPServer(('localhost', 8888), UDPHandler)
#th = threading.Thread(target=server.serve_forever)
#th.daemon = True
#th.start()

