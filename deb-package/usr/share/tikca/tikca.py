#!/usr/bin/env python3
import threading
import datetime
import time
import socket
import socketserver
import argparse
import os
from dateutil.tz import tzutc
import sys
import subprocess
import base64
from configobj import ConfigObj
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
import json
import signal
import icalendar
import logging


os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)-8s '
                    + '[%(filename)s:%(lineno)s:%(funcName)s()] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
# get config files
parser = argparse.ArgumentParser()
parser.add_argument("--tikcacfg", help="the tikca configfile", default='/etc/tikca.conf')
args = parser.parse_args()

TIKCFG = ConfigObj(args.tikcacfg, list_values=True)
globals()['TIKCFG'] = TIKCFG
from grabber import Grabber
from ingester import Ingester


# lock to serialize console output
#lock = threading.Lock()

import logging
# here, we're adding a file handler
try:
    console = logging.FileHandler(TIKCFG['logging']['fn'], mode='a', encoding="UTF-8")
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s %(levelname)-8s '
                                  + '[%(filename)s:%(lineno)s:%(funcName)s()] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)
except:
    logging.error("Cannot write log file into '%s' - using /tmp/tikca.log instead."%TIKCFG['logging']['fn'])
    console = logging.FileHandler("/tmp/tikca.log", mode='a', encoding="UTF-8")
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s %(levelname)-8s '
                                  + '[%(filename)s:%(lineno)s:%(funcName)s()] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)

# catch Ctrl+C
def siginthandler(signal, frame):
    logging.info("Shutting down TIKCA...")
    sys.exit(0)
signal.signal(signal.SIGINT, siginthandler)

#subdir_nextrecording = "aaaa" # why is that here

class TIKCAcontrol():
    global TIKCFG
    def __init__(self):
        logging.info("STARTING TIKCA ON HOST %s"%TIKCFG['agent']['name'])
        logging.info("STARTING CONTROL LOOPS...")
        # for episode management
        self.ANNOUNCEMENT = "No upcoming events."
        self.NEXTEPISODE = None
        self.NEXTPROPS = None
        self.NEXTEVENT = None
        self.NEXTSUBDIR = None
        self.CURSUBDIR = None
        self.STOPTS = None
        grabber.CURSUBDIR = None

        self.fetch_recording_data()
        self.tries = 0
        self.ultimatetries = 0

        # for udp server
        self.dest = (TIKCFG['udp']['ctrlstation'], int(TIKCFG['udp']['sendport']))
        self.sendsocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sendsocket.connect(self.dest)
        self.block_cmd = False
        self.udpconnectloop()
            #todo: Watchdog für pausierte Recordings - falls seit mehr als n sekunden pausiert, stop und ingest


    def udprc_setup(self):
        try:
            self.udpserver = socketserver.UDPServer(server_address=(TIKCFG['udp']['listenhost'],
                                                                int(TIKCFG['udp']['listenport'])),
                                                RequestHandlerClass=UDPHandler)
            return True
        except:
            logging.error("Could not start UDP server!")
            return False

    def udpconnectloop(self):
        try:
            self.sendsocket.connect(self.dest)
        except:
            logging.error("Cannot connect to UDP CtrlStation (%s:%s)"%
                          (TIKCFG['udp']['ctrlstation'], TIKCFG['udp']['sendport']))

    def send_email(self, subject, body):
        #todo dringend korrigieren: hier kommt kein bool!
        if TIKCFG['watch']['mail'] == "False":
            return True
        else:
            recipient = TIKCFG['watch']['mailto']
            TO = recipient if type(recipient) is list else [recipient]
            SUBJECT = subject
            TEXT = body

            message = """From: %s\nTo: %s\nSubject: %s\n\n%s
            """ % (sender, ", ".join(TO), SUBJECT, TEXT)

            try:
                server = smtplib.SMTP(TIKCFG['watch']['mailserver'], 587)
                server.ehlo()
                server.starttls()
                server.login(TIKCFG['watch']['maillogin'], TIKCFG['watch']['mailpassword'])
                server.sendmail(sender, TO, message)
                server.close()
                logging.debug("Sent mail to %s." % recipient)
            except:
                logging.error("Could not send mail to %s."%recipient)

    def get_outlet_state(self, n):
        if n in range(0, len(TIKCFG['outlet']['hosts'])):
            if len(TIKCFG['outlet']['users'][n]) > 0:
                url = "http://%s:%s@%s/statusjsn.js?components=1073741823"%(
                    TIKCFG['outlet']['users'][n], TIKCFG['outlet']['passwords'][n], TIKCFG['outlet']['hosts'][n])
            else:
                url = "http://%s/statusjsn.js?components=1073741823" % (
                    TIKCFG['outlet']['hosts'][n])
            try:
                jsonstring = ingester.curlreq(url).decode("UTF-8")
                jsondata = json.loads(jsonstring)
            # outlet nr counting from the config file starts with 1,
            # the json array stars with 0, so we have to compensate:
                jsonoutletnr = int(TIKCFG['outlet']['outletnrs'][n])-1
                return(jsondata['outputs'][jsonoutletnr]['state'],
                       jsondata['outputs'][jsonoutletnr]['name'])
            except:
                    logging.error("Cannot read outlet from '%s'"%url)
                    return(0, None)



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

            ingester.curlreq(url)

        else:
            logging.error("Cannot set weird state %s for outlet."%state)
        pass

    def watch_lights(self):
        # watches "recording"/"camera on" light in expected intervals and sets it on/off, if necessary
        # for usage of the gude network power outlets, see
        # http://wiki.gude.info/EPC_HTTP_Interface#Output_switching_.28easy_switching_command.29
        global grabber
        while True:
            # do we need the record light to be on or off?
            if grabber.get_recstatus() == "RECORDING":
                newstate = "1"
            else:
                newstate = "0"

            # we might have more than one outlet, that's the cause for the partly complicated function
            for n in range (0, len(TIKCFG['outlet']['hosts'])):
                if len(TIKCFG['outlet']['hosts'][n]) > 2:
                    (curstate, outletname) = self.get_outlet_state(n)
                    if not outletname == None:
                        if not int(curstate) == int(newstate):
                            self.set_outlet_state(n, newstate)
                            logging.info("Current state of outlet %s ('%s'): %s. Should be: %s. Setting." % (
                                        n, outletname, curstate, newstate))
            time.sleep(float(TIKCFG['outlet']['update_frequency']))

    def unix_ts(self, dtval):
        # return unix timestamp
        # this function is taken selfishly from Lars Kiesow's pyCA, I admit it. - PZ
        epoch = datetime.datetime(1970, 1, 1, 0, 0, tzinfo=tzutc())
        delta = (dtval - epoch)
        return delta.days * 24 * 3600 + delta.seconds

    def get_timestamp(self):
        # get a unix timestamp
        # this function is taken selfishly from Lars Kiesow's pyCA, I admit it. - PZ

        return self.unix_ts(datetime.datetime.now(tz=tzutc()))

    def get_schedule(self):
        # Get schedule for capture agent
        # this function is taken selfishly from Lars Kiesow's pyCA, I admit it. - PZ
        try:
            cutoff = ''
            lookahead = int(TIKCFG['agent']['cal_lookahead']) * 24 * 60 * 60
            if lookahead:
                cutoff = '&cutoff=%i' % ((self.get_timestamp() + lookahead) * 1000)
                calurl = '%s/recordings/calendars?agentid=%s%s' % \
                                (TIKCFG['server']['url'], TIKCFG['agent']['name'], cutoff)
            vcal = ingester.curlreq(calurl)
        except:
            logging.error('Could not get schedule from %s'%calurl)
            return None

        cal = None
        try:
            try:
                cal = icalendar.Calendar.from_string(vcal)
            except AttributeError:
                cal = icalendar.Calendar.from_ical(vcal)
            logging.debug("Got valid calendar.")
            #todo num of cal items (no, it's not len(cal))
        except:
            logging.error('Could not parse ical')
            #logging.error(traceback.format_exc())
            return None
        events = []
        for event in cal.walk('vevent'):
            dtstart = self.unix_ts(event.get('dtstart').dt.astimezone(tzutc()))
            dtend = self.unix_ts(event.get('dtend').dt.astimezone(tzutc()))
            uid = event.get('uid')

            # Ignore events that have already ended
            if dtend > self.get_timestamp():
                events.append((dtstart, dtend, uid, event))

        return sorted(events, key=lambda x: x[0])

    def fetch_recording_data(self):
        # asks server for upcoming recording
        # updates announcement
        # updates "NEXT..." variables
        global grabber, ingester
        logging.info("Status update: %s"%grabber.get_ocstatus())
        ingester.set_oc_castate(state=grabber.get_ocstatus())

        t = threading.Timer(float(TIKCFG['agent']['update_frequency']), self.fetch_recording_data)
        t.start()

        # If there is currently no event in this room:
        # Stop autostarted recording, if there's one.
        # TODO mal an eine andere stelle packen, wo es besser aufgehoben ist?
        if self.STOPTS != None:
            logging.debug("There is a stop date set: %i." % self.STOPTS)
            logging.debug("Current Unix TS: %i" % self.get_timestamp())
            if self.get_timestamp() > self.STOPTS:
                logging.debug("Stopping recording because Stopdate has been reached...")
                grabber.record_stop()
                self.STOPTS = None

        tmp = self.get_schedule()
        if tmp == None or len(tmp) == 0:
            logging.info("No upcoming events.")
            self.ANNOUNCEMENT = "No upcoming events."
            self.NEXTEPISODE = None
            self.NEXTPROPS = None
            self.NEXTEVENT = None
            self.NEXTSUBDIR = None

        else:
            nextevent = tmp[0] # in tmp, there is a list of events. [0] is the upcoming one.

            delta = nextevent[0] - time.time()
            d_start = datetime.datetime.fromtimestamp(nextevent[0]).strftime('%Y-%m-%d, %H:%M')
            d_end = datetime.datetime.fromtimestamp(nextevent[1]).strftime('%H:%M')
            d_timeuntil = datetime.timedelta(seconds=delta)
            uid = nextevent[3].get("uid")


            if delta < 90: # RIGHT NOW (or at least in 90 seconds), there is an event in this room!
                self.ANNOUNCEMENT = "Current event in this room: '%s' (ends at %s)"%\
                                    (nextevent[3].get("SUMMARY"), d_end)
                logging.debug(self.ANNOUNCEMENT)
                self.NEXTEPISODE = base64.b64decode(str(nextevent[3].get("attach")[0])).decode("utf-8")  # episode.xml
                self.NEXTPROPS = base64.b64decode(str(nextevent[3].get("attach")[1])).decode("utf-8")
                grabber.NEXTWFIID = uid
                self.NEXTSUBDIR = uid

                if not grabber.get_recstatus() in ("RECORDING", "PAUSED", "STARTING", "PAUSING"):
                    self.CURSUBDIR = self.NEXTSUBDIR
                    grabber.CURSUBDIR = self.NEXTSUBDIR
                # We need two variables for that. Because: Imagine recording A is scheduled from 10:00 till 10:30,
                # recording B from 10:31 to 11:00. When A takes until 10:35, stuff gets written into B's directory from 10:30 on.
                # So we make sure that NEXTSUBDIR gets overwritten when there's another recording, CURSUBDIR only gets overwritten
                # when there's no recording going on *right now*. Complicated, huh.

                self.NEXTEVENT = nextevent

                # if a) automatic recordings are True,
                # b) there is nothing being recorded right now and we're not starting the recording right now,
                # c) there is a scheduled recording for this CA: start recording!
                # TODO Dringend fixen: Hier kommt kein bool...!
                if (grabber.get_recstatus() == "IDLE" or grabber.get_recstatus() == "ERROR")\
                and TIKCFG['capture']['autostart'] == "True" \
                and not grabber.get_recstatus() == "STARTING":
                    logging.debug("Trying to start recording (Autostart is set to True)...")
                    grabber.setup_recorddir(subdir=self.NEXTSUBDIR, epidata=self.NEXTEPISODE)
                    # set a stop date
                    self.STOPTS = int(nextevent[1])
                    logging.debug("Creating pipeline in standby...")
                    grabber.pipe_create()
                    logging.debug("Starting recording.")
                    grabber.record_start()
                    logging.debug("Telling Opencast core that WFIID %s is capturing."%uid)
                    ingester.set_oc_recstate("capturing", uid)


            else:
                # currently, there is no event. But soon there will be one.
                # Things to do:
                # leave everything ready for unscheduled recordings
                logging.debug("Next event ('%s', UID %s) occurs from %s to %s. This is in %s."%
                              (nextevent[3].get("SUMMARY"), uid, d_start, d_end, str(d_timeuntil).split(".")[0]))
                self.ANNOUNCEMENT = "Next event: '%s' at %s."%\
                                (nextevent[3].get("SUMMARY"), d_start)
                self.NEXTEPISODE = None
                self.NEXTPROPS = None
                grabber.NEXTWFIID = None
                self.NEXTSUBDIR = None
                self.CURSUBDIR = ""
                grabber.CURSUBDIR = ""




        #return (self.NEXTEPISODE, self.NEXTPROPS) # braucht man das?
        return True


    def sendtoctrlstation(self, data):
        try:
            #logging.debug("Sending UDP message: '%s'"%data)
            self.sendsocket.sendall(data.encode("utf-8"))
        except ConnectionRefusedError:
            logging.error("Cannot send UDP message %s to %s:%s"%(data, TIKCFG['udp']['ctrlstation'],
                                                                int(TIKCFG['udp']['sendport'])))


    def watch_recstart(self):
        #deprecated!!!
        global grabber
        GObject.threads_init()
        Gst.init(None)
        # We're only called when a record should be starting.
        # Therefore, if the pipeline is in Gst.State.PAUSED, we're not running while we should be.
        a = """if grabber.pipeline.get_state(False)[1] == Gst.State.PAUSED and self.ultimatetries <= 5:
            # try again to push the PLAY-Button
            #grabber.pipeline.set_state(Gst.State.PLAYING) # DAS könnte der ultimative Desync-Fehler sein.
            t = threading.Timer(1.0, self.watch_recstart)
            t.start()
            # ... and watch over it in one second again
            self.tries += 1
            # try three times to start by setting "Gst.State.PLAYING"

            if self.tries > 8:
                # if that doesn't help, kill the pipeline and start all over again
                logging.error("Waited 8 seconds to start pipeline")
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
                self.ultimatetries += 1"""
        try:
            grabber.pipeline
            if grabber.pipeline.get_state(False)[1] == Gst.State.PLAYING:
                # we're finally happy: The pipeline is running
                logging.info("Pipeline running!")
                grabber.set_recstatus("RECORDING")
                grabber.set_ocstatus("capturing")
                return True
        except:
            pass
            # billiger workaround, bis es nur noch einen record start watcher gibt.
        self.t = threading.Timer(0.2, mycontrol.watch_recstart)
        self.t.start()

        a = """if self.ultimatetries > 5:
            # we tried five times to remove and rebuild the pipeline; this has to end.
            logging.error("FATAL ERROR! Tried five times to remove and rebuild pipeline. Not trying anymore.")
            grabber.set_recstatus("IDLE")
            # todo: set state to error
            self.ultimatetries = 0"""



    def watch_length(self):
        # stop recording when maximum length is reached
        # todo testen ausführlich
        if grabber.get_recstatus() == "RECORDING" and grabber.recordingsince != None:
            ts_begin = time.mktime(grabber.recordingsince.timetuple())
            ts_now = time.mktime(datetime.datetime.now(tz=tzutc()).timetuple())
            logging.debug("Recording since %s min (max: %s min)."%
                          (round((ts_now-ts_begin)/60), TIKCFG['capture']['maxduration']))

            if ts_now-ts_begin > float(TIKCFG['capture']['maxduration'])*60:
                grabber.record_stop()
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
        global ingester
        if ingester.df() < float(TIKCFG['watch']['df_space']):
            logging.error("There are less than %s MB available on recording media."%TIKCFG['watch']['df_space'])
            self.send_email("TIKCA %s: Not enough space on capture drive" % TIKCFG['agent']['name'], "")
        if not os.access(TIKCFG['capture']['directory'], os.W_OK):
            logging.error("It seems impossible to write into capture directory '%s'. Check permissions!"%TIKCFG['capture']['directory'])
            self.send_email("TIKCA %s: Cannot write into capture dir"%TIKCFG['agent']['name'], "")
        self.tp = threading.Timer(float(TIKCFG['watch']['df_time']), self.watch_freespace)
        self.tp.start()

class UDPHandler(socketserver.BaseRequestHandler):
    messagecount = 0
    lastmsg = None

    def handle(self):
        global grabber, mycontrol, ingester

        data = self.request[0]
        socket = self.request[1]
        data = data.decode('UTF-8')

        # To stop messing up the logfile, we will only be reporting every 30th occurence of the same message.
        if self.lastmsg == data:
            self.messagecount+=1
            if self.messagecount > 30:
                logging.debug("UDP message (30 times) from %s: '%s'"%(format(self.client_address[0]), data.strip()))
                self.lastmsg = data
                self.messagecount = 0
        else:
            logging.debug("UDP message from %s: '%s'" % (format(self.client_address[0]), data.strip()))
            self.lastmsg = data
        # TODO: Messagecount in config file

        self.lastmsg = data

        # make sure only one thing is done at a time
        #todo does this make sense?
        #if mycontrol.block_cmd:
        #    logging.error("Too much for me.")
        #    return
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
                mycontrol.send_email("TIKCA on %s: Recording started by command."%TIKCFG['agent']['name'], "")
                mycontrol.block_cmd = False
                logging.debug("Creating pipeline in standby...")
                grabber.pipe_create()
                logging.debug("Starting recording.")
                grabber.record_start()
                logging.debug("Telling Opencast core that WFIID %s is capturing."%grabber.NEXTWFIID)
                ingester.set_oc_recstate("capturing", grabber.NEXTWFIID)

            elif grabber.get_recstatus() == "PAUSED":
                logging.debug("Restarting recording from pause.")
                grabber.record_start()
                mycontrol.block_cmd = False
            else:
                logging.error("Got UDP command to START recording while recording is going on already.")
            mycontrol.block_cmd = False

        if ":STOP" in data:
            #if (grabber.get_recstatus() == "PAUSED" or grabber.get_recstatus() == "RECORDING"):
            mycontrol.send_email("TIKCA on %s: Recording stopped by command." % TIKCFG['agent']['name'], "")
            mycontrol.block_cmd = True
            logging.debug("Getting UDP command to stop recording")
            logging.info("Stopping recording")
            grabber.record_stop()
            ingester.write_dirstate(grabber.RECDIR, "STOPPED")
            mycontrol.CURSUBDIR = None
            grabber.CURSUBDIR = None
            mycontrol.block_cmd = False

#            else:
#                logging.error("Got UDP command to STOP recording, but no recording is going on.")


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

ingester = Ingester()

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

if len(TIKCFG['outlet']['hosts']) > 0:
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
