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
        self.NEXTUID = None
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
        self.set_lights()

        #todo: Watchdog für pausierte Recordings - falls seit mehr als n sekunden pausiert, stop und ingest

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

    def get_outlet_state(self):
        # go through all configured outlets and check their states
        for n in range(1, len(TIKCFG['outlet']['hosts'])):
            url = "http://%s:%s@%s/statusjsn.js?components=1073741823"%(
                TIKCFG['outlet'][n]['user'], TIKCFG['outlet'][n]['password'], TIKCFG['outlet'][n]['host'])
            logging.debug(url)

            try:
                jsonstring = self.http_request(url).decode("UTF-8")
                jsondata = json.loads(jsonstring)
                print(jsondata)
            except Exception:
                return False


    def set_outlet_state(self, state):
        if state in [0, 1]:
            for n in range(1, len(TIKCFG['outlet']['hosts'])):
                url = "http://%s:%s@%s/ov.html?cmd=1&p=%s&s=%s"%(
                    TIKCFG['outlet'][n]['user'], TIKCFG['outlet'][n]['password'], TIKCFG['outlet'][n]['host'],
                    TIKCFG['outlet'][n]['outletnr'], state
                )
                print(self.http_request(url))

        pass

    def set_lights(self):
        # sets recording/camera light in expected intervals
        global grabber, mycontrol
        if len(TIKCFG['outlet']['host1']) > 7:
            t = threading.Timer(int(TIKCFG['outlet']['update_frequency']), self.set_lights)
            t.start()

            if grabber.get_recstatus() == "RECORDING":
                newstat = "1"
            else:
                newstat = "0"

            self.get_outlet_state()
            # http://wiki.gude.info/EPC_HTTP_Interface#Output_switching_.28easy_switching_command.29
            #todo: check whether all outlets are in reasonable state

            # old version:
            #logging.debug("Net outlet update: %s|%s set to %s."
            #             %(TIKCFG['outlet']['host1'], TIKCFG['outlet']['reclight1'], newstat))
            #cmd = "snmpset -v2c -mALL -c private %s %s integer %s" %\
            #      (TIKCFG['outlet']['host1'], TIKCFG['outlet']['reclight1'], newstat)
            #subprocess.call(shlex.split(cmd),stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            #cmd2 = "snmpset -v2c -mALL -c private %s %s integer %s" %\
            #      (TIKCFG['outlet']['host2'], TIKCFG['outlet']['reclight2'], newstat)
            #subprocess.call(shlex.split(cmd2),stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)



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
            self.NEXTUID = None
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
            if not grabber.get_recstatus() in ["RECORDING", "PAUSED", "STARTING", "PAUSING]":
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
        self.NEXTUID = uid

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
        import gi
        from gi.repository import GObject, Gst
        gi.require_version('Gst', '1.0')
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



    def lengthwatch(self):
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

        self.tp = threading.Timer(20.0, self.lengthwatch)
        self.tp.start()

    def hostcheck(self, host):
        status, result = subprocess.getstatusoutput("ping -c2 -w2 " + str(host))
        if status == 0:
            logging.debug("Host %s is up!"%str(host))
            return True
        else:
            logging.debug("Host %s is down!"%str(host))
            return False

    def hostcheckloop(self):
        # check hosts via ping whether they are up
        # if we are recording, we suppose that the hosts in question are up, so we're not testing
        if grabber.get_recstatus() != "RECORDING":
            if len(TIKCFG['capture']['src1_adminip']) > 6:
                logging.debug("Checking whether host %s is up..."%TIKCFG['capture']['src1_adminip'])
                if not (self.hostcheck(TIKCFG['capture']['src1_adminip'])):
                    logging.error("Host %s is down!"%TIKCFG['capture']['src1_adminip'])

            try:
                TIKCFG['capture']['src2_adminip']
                if len(TIKCFG['capture']['src2_adminip']) > 6:
                    logging.debug("Checking whether host %s is up..."%TIKCFG['capture']['src2_adminip'])
                    if not (self.hostcheck(TIKCFG['capture']['src2_adminip'])):
                        logging.error("Host %s is down!"%TIKCFG['capture']['src2_adminip'])
            except KeyError:
                pass
        self.tp = threading.Timer(8.0, self.hostcheckloop)
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



th2 = threading.Thread(target=mycontrol.hostcheckloop)
th2.daemon = True
th2.start()

lw = threading.Thread(target=mycontrol.lengthwatch)
lw.daemon = True
lw.start()

dfw = threading.Thread(target=mycontrol.dfwatch)
dfw.daemon = True
dfw.start()



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

