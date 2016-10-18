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

TIKCONFIG = ConfigObj('/etc/tikca.conf')
globals()['TIKCONFIG'] = TIKCONFIG
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
console = logging.FileHandler(TIKCONFIG['logging']['fn'], mode='a', encoding="UTF-8")
console.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s %(levelname)-8s '
                              + '[%(filename)s:%(lineno)s:%(funcName)s()] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
# tell the handler to use this format
console.setFormatter(formatter)
# add the handler to the root logger
logging.getLogger('').addHandler(console)


#subdir_nextrecording = "aaaa" # why is that here

class TIKCAcontrol():
    global TIKCONFIG
    def __init__(self):
        logging.info("STARTING CONTROL LOOPS...")
        # for episode management
        self.ANNOUNCEMENT = "No upcoming events."
        self.NEXTEPISODE = None
        self.NEXTPROPS = None
        self.NEXTEVENT = None
        self.LASTSUBDIR = None
        self.NEXTSUBDIR = None
        self.NEXTUID = None
        self.get_current_recording_data()
        self.tries = 0
        self.ultimatetries = 0

        # for udp server
        self.dest = (TIKCONFIG['udp']['ctrlstation'], int(TIKCONFIG['udp']['sendport']))
        self.sendsocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sendsocket.connect(self.dest)
        self.block_cmd = False
        self.udpconnectloop()
        self.set_lights()

        #todo: Watchdog für pausierte Recordings - falls seit mehr als n sekunden pausiert, stop und ingest

    def ingestloop(self):
        # TODO Ingestwatcher bauen
        # 1. watch over the recordings directory,
        # 2. get an idea which recordings still have to be ingested
        # 3. ingest those who need ingesting every n minutes (conf'd in TIKCA)
        logging.info("ingest")

    def udprc_setup(self):
        try:
            self.udpserver = socketserver.UDPServer(server_address=(TIKCONFIG['udp']['listenhost'],
                                                                    int(TIKCONFIG['udp']['listenport'])),
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
                          (TIKCONFIG['udp']['ctrlstation'], TIKCONFIG['udp']['sendport']))

    def set_lights(self):
        # sets recording/camera light in expected intervals
        global grabber, mycontrol
        if len(TIKCONFIG['outlet']['host1']) > 7:
            t = threading.Timer(int(TIKCONFIG['outlet']['update_frequency']), self.set_lights)
            t.start()

            if grabber.get_recstatus() == "RECORDING":
                newstat = "1"
            else:
                newstat = "0"

            #logging.debug("Net outlet update: %s|%s set to %s."
            #             %(TIKCONFIG['outlet']['host1'], TIKCONFIG['outlet']['reclight1'], newstat))
            cmd = "snmpset -v2c -mALL -c private %s %s integer %s" %\
                  (TIKCONFIG['outlet']['host1'], TIKCONFIG['outlet']['reclight1'], newstat)
            subprocess.call(shlex.split(cmd),stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            cmd2 = "snmpset -v2c -mALL -c private %s %s integer %s" %\
                  (TIKCONFIG['outlet']['host2'], TIKCONFIG['outlet']['reclight2'], newstat)
            subprocess.call(shlex.split(cmd2),stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)



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
        d_start_wo_year = datetime.datetime.fromtimestamp(nextevent[0]).strftime('%H:%M')
        d_end = datetime.datetime.fromtimestamp(nextevent[1]).strftime('%H:%M')
        d_dur = str(datetime.timedelta(seconds=delta)) #todo: ordentlich formatieren, außerdem wird die zeit irgendwie geringer während der ansage...
        uid = nextevent[3].get("uid")

        if delta < 0: # RIGHT NOW, there is an event in this room!
            self.ANNOUNCEMENT = "Current event in this room: '%s' (ends at %s)"%\
                                (nextevent[3].get("SUMMARY"), d_end)
            logging.debug(self.ANNOUNCEMENT)
            self.NEXTSUBDIR = datetime.datetime.fromtimestamp(nextevent[0]).strftime('%m%d') + "_" + uid
            self.NEXTEVENT = nextevent

            # if a) automatic recordings are True,
            # b) there is nothing being recorded right now and we're not starting the recording right now,
            # c) there is a scheduled recording for this CA: start recording!
            if (grabber.get_recstatus() == "IDLE" or grabber.get_recstatus() == "ERROR")\
                and TIKCONFIG['capture']['autostart'] == True \
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
            # set up announcement for the next event
            logging.debug("Next event ('%s', UID %s) occurs from %s to %s. This is in %i seconds."%
                          (nextevent[3].get("SUMMARY"), uid, d_start_wo_year, d_end, int(delta)))
            self.ANNOUNCEMENT = "Next event: '%s' at %s."%\
                            (nextevent[3].get("SUMMARY"), d_start)
            self.LASTSUBDIR = self.NEXTSUBDIR
            self.NEXTSUBDIR = None

            # and, if there was a recording going on because of autostart, stop it
            # TODO FETTES TODO: AUTOSTOP FUNZT NICHT!!!!!!!!!!!!!! (da fehlt ja auch ne zeitliche bedingung)
            if grabber.get_recstatus() == "RECORDING":
                logging.debug("Stopping autostarted recording...")
                grabber.record_stop()
                self.ingest(CONFIG['capture']['directory'] + "/" + self.LASTSUBDIR)



        self.NEXTEPISODE = base64.b64decode(str(nextevent[3].get("attach")[0])).decode("utf-8")  # episode.xml
        self.NEXTPROPS = base64.b64decode(str(nextevent[3].get("attach")[1])).decode("utf-8")

#        self.NEXTSUBDIR = datetime.datetime.fromtimestamp(nextevent[0]).strftime('%m%d') + "_" + uid
        self.NEXTUID = uid

        return (self.NEXTEPISODE, self.NEXTPROPS)


    def sendtoctrlstation(self, data):
        try:
            #logging.debug("Sending UDP message: '%s'"%data)
            self.sendsocket.sendall(data.encode("utf-8"))
        except ConnectionRefusedError:
            logging.error("Cannot send UDP message %s to %s:%s"%(data, TIKCONFIG['udp']['ctrlstation'],
                                                                int(TIKCONFIG['udp']['sendport'])))

    def analyze_stats(self, dirname):
        # B is for black, P for presentation, C for camera
        logging.debug("Analyzing directory '%s'"%dirname)
        fns = []
        flavors = []
        try:
            with open(dirname + "/" + "state.log", 'r') as f:
                lineslist = [tuple(line) for line in csv.reader(f)]
                src1list = list(zip(*lineslist))[1]
                src1_stats = {"B": src1list.count("B"), "C": src1list.count("C"), "P": src1list.count("P")}
                src1_max = max(src1_stats, key=src1_stats.get)
                src2list = list(zip(*lineslist))[2]
                src2_stats = {"B": src2list.count("B"), "C": src2list.count("C"), "P": src2list.count("P")}
                src2_max = max(src2_stats, key=src2_stats.get)

            logging.debug("Stats for source 1 (B|C|P): \t %s|%s|%s"%
                          (src1_stats["B"], src1_stats["C"],src1_stats["P"]))
            logging.debug("Stats for source 2 (B|C|P): \t %s|%s|%s"%
                          (src2_stats["B"], src2_stats["C"],src2_stats["P"]))



            # there is room for error: if somebody shows content less than 4 seconds on one stream, it's counted
            # as empty
            if src1_stats["C"] + src1_stats["P"] - src1_stats["B"] < 4:
                src1_flavor = None
            else:
                fns.append(dirname + "/" + TIKCONFIG['capture']['src1_fn_vid'])
                if src1_max == "C":
                    src1_flavor = "presenter/source"
                else:   # if max = P or max = B
                    src1_flavor = "presentation/source"


            if src2_stats["C"] + src2_stats["P"] - src2_stats["B"] < 10:
                src2_flavor = None
            else:
                try:
                    TIKCONFIG['capture']['src2_fn_vid']
                    fns.append(dirname + "/" + TIKCONFIG['capture']['src2_fn_vid'])
                    if src2_max == "C":
                        src2_flavor = "presenter/source"
                    else:   # if max = P or max = B
                        src2_flavor = "presentation/source"
                except KeyError:
                    logging.debug("No filename for SRC2 set. Ignoring.")
                    src2_flavor = None


            # We cannot have 2x"presenter/source", neither 2x"presentation/source".
            if src1_flavor == "presenter/source" and src2_flavor == "presenter/source":
                src2_flavor = "presentation/source"
            elif src1_flavor == "presentation/source" and src2_flavor == "presentation/source":
                src2_flavor = "presentation2/source"

            logging.info("Flavor of source 1: '%s'"%src1_flavor)
            logging.info("Flavor of source 2: '%s'"%src2_flavor)
            if not src1_flavor == None: flavors.append(src1_flavor)
            if not src2_flavor == None: flavors.append(src2_flavor)

            # take one sound file: that which has less 'B' marks
            if src1_stats['C'] + src1_stats['P'] >= src2_stats['C'] + src2_stats['P']:
                fns.append(dirname + "/" + TIKCONFIG['capture']['src1_fn_aud'])
            else:
                try:
                    TIKCONFIG['capture']['src2_fn_aud']
                    fns.append(dirname + "/" + TIKCONFIG['capture']['src2_fn_aud'])
                except KeyError:
                    # send the SRC1 audio file even if it is "black", so we've got something to send
                    fns.append(dirname + "/" + TIKCONFIG['capture']['src1_fn_aud'])
            flavors.append("presenter/source")



        except FileNotFoundError:
            logging.error("No log file in directory %s! Using default flavor names."%dirname)
            try:
                TIKCONFIG['capture']['src1_fn_aud']
                TIKCONFIG['capture']['src1_fn_vid']
                fns.append(dirname + "/" + TIKCONFIG['capture']['src1_fn_aud'])
                fns.append(dirname + "/" + TIKCONFIG['capture']['src1_fn_vid'])
                flavors.append(TIKCONFIG['capture']['src1_stdflavor'])
                flavors.append(TIKCONFIG['capture']['src1_stdflavor'])

            except:
                logging.error("Error in adding SRC1's flavors/filenames to ingest list!")
                return list()
            try:
                TIKCONFIG['capture']['src2_fn_aud']
                TIKCONFIG['capture']['src2_fn_vid']
                fns.append(dirname + "/" + TIKCONFIG['capture']['src2_fn_aud'])
                fns.append(dirname + "/" + TIKCONFIG['capture']['src2_fn_vid'])
                flavors.append("presenter/backup")
                flavors.append(TIKCONFIG['capture']['src2_stdflavor'])
            except:
                # we're fine with not having SRC2 defined
                pass

            logging.debug("File list: %s, %s"%(flavors, fns))
            # Attention! In Python2 (in which ca.py is programmed), zip() returns a iteratable list.
            # In Python3 though, this returns an iterator. So we need to fix this by doing list(zip()).
            return list(zip(flavors, fns))


    def get_instance(self, uid):

        url = "https://%s/workflow/instance/%s.json"%(CONFIG['server']['url'], uid)
        logging.debug(url)

        try:
            jsonstring = self.http_request(url).decode("UTF-8")
        except Exception:
            return False

        jsondata = json.loads(jsonstring)
        return jsondata['workflow']['mediapackage']['series']

    def ingest(self, subdir):
        # generate track infos from dir

        logging.info("Analyzing and ingesting dir \"%s\"..."%subdir)
        tracks = self.analyze_stats(subdir)
        if len(tracks) > 1:
            logging.debug("Tracklist returned from analyze step: %s, %s"%(subdir, list(tracks)))
        else:
            logging.error("Failed to analyze recording data in %s."%subdir)

        #if not list(tracks) == []:
        #todo abfragen ob tracks da sind
        # if there was a scheduled recording, NEXTPROPS and NEXTUID hold details.
        # if not, they are None
        if not self.NEXTPROPS == None:
            wf_def, wf_conf = ca.get_config_params(self.NEXTPROPS)
            uid = self.NEXTUID
        else:
            wf_def = TIKCONFIG['unscheduled']['workflow']
            wf_conf = ''
            uid = "Unscheduled-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

        logging.debug("PyCA ingest says: %s"%
                      ca.ingest(tracks, CONFIG['capture']['directory'] + "/" + subdir, uid, wf_def, wf_conf))

        return True
        #else:
        #    logging.error("NoTracksFoundError")
        #    return False

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

    def dfwatch(self):
        # TODO fertigmachen; außerdem: Tu das beim Start des TIKCA. Außerdem gucken, ob das Verzeichnis überhaupt existiert
        # print error message and deliver mail if no space left to record
        statvfs = os.statvfs(CONFIG['capture']['directory'])
        MB_free = round(statvfs.f_frsize * statvfs.f_bavail/1024/1024)
        #logging.debug("MB available: %i"%MB_free)
        if MB_free < 3000:
            logging.error("LOW SPACE ON HARD DRIVE! ONLY %i MB left!"%MB_free)
            # todo Mail schicken
        dfwtimer = threading.Timer(300.0, self.dfwatch)
        dfwtimer.start()

    def lengthwatch(self):
        # stop recording when maximum length is reached

        if grabber.get_recstatus() == "RECORDING" and grabber.recordingsince != None:
            ts_begin = time.mktime(grabber.recordingsince.timetuple())
            ts_now = time.mktime(datetime.datetime.now().timetuple())
            logging.debug("Recording since %s min (max: %s min)."%
                          (round((ts_now-ts_begin)/60), TIKCONFIG['capture']['maxduration']))

            if ts_now-ts_begin > float(TIKCONFIG['capture']['maxduration'])*60:
                grabber.record_stop()
                ingest_thread = threading.Thread(target=self.ingest, args=(grabber.RECDIR,))
                ingest_thread.start()
                logging.error("Stopping the recording since maximum duration of %s min has been reached."
                              %TIKCONFIG['capture']['maxduration'])

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

        # if we are recording, we suppose that the hosts in question are up
        if grabber.get_recstatus() != "RECORDING":
            if len(TIKCONFIG['capture']['src1_adminip']) > 6:
                logging.debug("Checking whether host %s is up..."%TIKCONFIG['capture']['src1_adminip'])
                if not (self.hostcheck(TIKCONFIG['capture']['src1_adminip'])):
                    logging.error("Host %s is down!"%TIKCONFIG['capture']['src1_adminip'])

            try:
                TIKCONFIG['capture']['src2_adminip']
                if len(TIKCONFIG['capture']['src2_adminip']) > 6:
                    logging.debug("Checking whether host %s is up..."%TIKCONFIG['capture']['src2_adminip'])
                    if not (self.hostcheck(TIKCONFIG['capture']['src2_adminip'])):
                        logging.error("Host %s is down!"%TIKCONFIG['capture']['src2_adminip'])
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
        if not self.client_address[0] == TIKCONFIG['udp']['ctrlstation']:
            logging.error("Not processing UDP message from %s: Origin is not %s."%(self.client_address[0],
                                                                                   TIKCONFIG['udp']['ctrlstation']))
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
                # is there no scheduled event? -> make recdir with unique name
                # is solved in grabber.setup_recorddir
                grabber.setup_recorddir(subdir=mycontrol.NEXTSUBDIR, epidata=mycontrol.NEXTEPISODE)

                if grabber.standby():
                    #grabber.record_start()
                    mycontrol.block_cmd = False
                    self.t = threading.Timer(1.0, mycontrol.watch_recstart) # Diese Version funzt 20160217
                    self.t.start() # Diese Version funzt 20160217

                else:
                    logging.error("Grabber failed to enter standby mode before recording!")

            elif grabber.get_recstatus() == "PAUSED":
                #grabber.record_start()
                self.t = threading.Timer(1.0, mycontrol.watch_recstart) # Diese Version funzt 20160217
                self.t.start() # Diese Version funzt 20160217

            else:
                logging.error("Got UDP command to START recording while recording is going on already.")

            mycontrol.block_cmd = False

        if ":STOP" in data and (grabber.get_recstatus() == "PAUSED" or grabber.get_recstatus() == "RECORDING"):
            mycontrol.block_cmd = True
            logging.debug("Getting UDP command to stop recording")
            if grabber.get_pipestatus() == "capturing":
                logging.info("Stopping recording")
                grabber.record_stop()
                ingest_thread = threading.Thread(target=mycontrol.ingest, args=(grabber.RECDIR,))
                ingest_thread.start()

            else:
                logging.error("Got UDP command to STOP recording, but no recording is going on.")
            mycontrol.block_cmd = False


        if ":PAUSE" in data:
            mycontrol.block_cmd = True
            logging.debug("Getting UDP command to pause recording")
            #todo pause ist irgendwie borken - macht 0 byte-files
            if grabber.get_pipestatus() == "capturing":
                grabber.record_pause()
            else:
                logging.error("Got UDP command to PAUSE recording, but no recording is going on.")
            mycontrol.block_cmd = False

        if ":STATE" in data:
            #logging.debug("Received STATE message: %s"%data.strip())
            # write state of Sendeleitung 1 and 2 to file, if recording
            # if not, do nothing
            grabber.write_states(data.split(" ")[1])
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

