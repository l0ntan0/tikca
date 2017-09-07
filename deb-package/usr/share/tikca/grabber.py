#!/usr/bin/python3

import gi
import os
from configobj import ConfigObj
import datetime
import threading
import time
from gi.repository import GObject, Gst
import argparse
gi.require_version('Gst', '1.0')
GObject.threads_init()
Gst.init(None)
import sys
import logging
from ingester import Ingester



os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))
#TODO: Check whether config files are there, and maybe include option to give config files in command line
parser = argparse.ArgumentParser()
parser.add_argument("--tikcacfg", help="the tikca configfile", default='/etc/tikca.conf')
args = parser.parse_args()

#print(args.tikcacfg)
TIKCFG = ConfigObj(args.tikcacfg, list_values=True)
ingester = Ingester()

class Grabber:
    global TIKCFG
    def __init__(self):
        logging.info("STARTING TIKCA-GRABBER")
        self.stoprequest = threading.Event()
        self.RECDIR = None
        self.retrycount = 0
        self.recordingsince = None
        self.set_recstatus("IDLE")
        self.set_ocstatus("idle")
        self.NEXTWFIID = None
        self.pipes = {}
        self.buses = {}
        
        logging.info("Grabber initialized")


    def check_srcstr(self, sourcestring):
        #todo implement a function that validates "udp://239.25.1.34:4444", "tcp://192.168.1.1:1234" and the like

        return True

    def pipe_create(self):
        logging.debug("Setting up pipeline")
        # todo: only do this when state != playing or starting
        # Key to happiness for demuxing one stream:
        # gst-launch-1.0 -e udpsrc uri=udp://239.25.1.34:4444 ! video/mpegts, systemstream=\(boolean\)true, packetsize=\(int\)188 !
        # tsdemux name=d ! queue ! video/x-h264 ! filesink location="out.h264" d. ! queue ! audio/mpeg, mpegversion=\(int\)2,
        # stream-format=\(string\)adts ! filesink location="out.aac"

        prot_src1 = TIKCFG['sources']['src1']['uri'].split("://")[0]
        if not self.check_srcstr(TIKCFG['sources']['src1']['uri']):
            logging.error("Definition of capture source 1 is not working! Definition is:")
            logging.error(TIKCFG['sources']['src1']['uri'])
            return False

        if prot_src1 == "udp":
            self.pipes['src1'] = Gst.parse_launch("udpsrc uri=%s ! filesink location=%s"%
                                          (TIKCFG['sources']['src1']['uri'],
                                           self.RECDIR + "/" + TIKCFG['sources']['src1']['fn_orig']))
            print(self.pipes)

            # todo rtsp support wieder rein
            # todo evtl das hier wieder rein?
            # 'video/mpegts, systemstream=(boolean)true, packetsize=(int)188'
            #self.buses['src1'].connect('message::error', self.on_error)
            #self.buses['src1'].enable_sync_message_emission()
            #self.buses['src1'].connect('sync-message::element', self.on_sync_message)

            #self.src1 = Gst.ElementFactory.make('udpsrc', "udpsrc1")
            #self.src1.set_property('multicast-iface', TIKCFG['capture']['src1_iface'])
            #self.src1.set_property('buffer-size', 0)

        elif prot_src1 == "tcp":
            src1host = TIKCFG['sources']['src1']['uri'].split("://")[1].split(":")[0]
            src1port = TIKCFG['sources']['src1']['uri'].split(":")[2]
            self.pipes['src1'] = Gst.parse_launch("tcpclientsrc host=%s port=%s ! filesink location=%s"%
                                                  (src1host, src1port,
                                                   self.RECDIR + "/" + TIKCFG['sources']['src1']['fn_orig']))

        # add bus - needed for EOS treatment
        self.buses['src1'] = self.pipes['src1'].get_bus()
        self.buses['src1'].add_signal_watch()



        # add second stream
        try:
            prot_src2 = TIKCFG['sources']['src2']['uri'].split("://")[0]
            if not self.check_srcstr(TIKCFG['sources']['src2']['uri']):
                logging.error("Definition of capture source 1 is not working! Definition is:")
                logging.error(TIKCFG['sources']['src2']['uri'])
                return False

            if prot_src2 == "udp":
                self.pipes['src2'] = Gst.parse_launch("udpsrc uri=%s ! filesink location=%s" %
                                                      (TIKCFG['sources']['src2']['uri'],
                                                       self.RECDIR + "/" + TIKCFG['sources']['src2']['fn_orig']))
                print(self.pipes)

                # todo rtsp support wieder rein
                # todo evtl das hier wieder rein?
                # 'video/mpegts, systemstream=(boolean)true, packetsize=(int)188'
                # self.buses['src1'].connect('message::error', self.on_error)
                # self.buses['src1'].enable_sync_message_emission()
                # self.buses['src1'].connect('sync-message::element', self.on_sync_message)

                # self.src2 = Gst.ElementFactory.make('udpsrc', "udpsrc2")
                # self.src2.set_property('multicast-iface', TIKCFG['capture']['src2_iface'])
                # self.src2.set_property('buffer-size', 0)

            elif prot_src2 == "tcp":
                src2host = TIKCFG['sources']['src2']['uri'].split("://")[1].split(":")[0]
                src2port = TIKCFG['sources']['src2']['uri'].split(":")[2]
                self.pipes['src2'] = Gst.parse_launch("tcpclientsrc host=%s port=%s ! filesink location=%s" %
                                                      (src2host, src2port,
                                                       self.RECDIR + "/" + TIKCFG['sources']['src2']['fn_orig']))

            # add bus - needed for EOS treatment
            self.buses['src2'] = self.pipes['src2'].get_bus()
            self.buses['src2'].add_signal_watch()

        except KeyError:
            logging.debug("No SRC2 defined. Setting up pipeline only for SRC1.")

        return True

    def on_pad_added(self, element, src_pad, q):
        # deprecated
        # originally needed because the demuxer dynamically added pads dynamically
        # here, the pads are selected due to the naming/content
        logging.debug("Pads added called!")
        caps = src_pad.query_caps(None)
        name = caps.to_string()
        logging.debug("Pads added: %s"%(name))


        #if name.startswith("application/x-rtp, media=(string)video"):
        #    sink_pad = q[1].get_static_pad('sink')
        #    src_pad.link(sink_pad)
        #    logging.debug("Video queue pad linked (RTP): %s"%sink_pad)

        #elif name.startswith("application/x-rtp, media=(string)audio"):
        #    sink_pad = q[0].get_static_pad('sink')
        #    src_pad.link(sink_pad)
        #    logging.debug("Audio queue pad linked (RTP): %s"%sink_pad)

        if name.startswith("video/x-h264"):
            sink_pad = q[1].get_static_pad('sink')
            src_pad.link(sink_pad)
            logging.debug("Video queue pad linked (MPEGTS): %s"%sink_pad)

        elif name.startswith("audio/mpeg"):
            sink_pad = q[0].get_static_pad('sink')
            src_pad.link(sink_pad)
            logging.debug("Audio queue pad linked (MPEGTS): %s"%sink_pad)

    def checkstarted(self):
        logging.info("Checking whether recording has started or not...")
        if self.get_recstatus() == "PAUSED" or self.get_recstatus() == "STOPPED" or self.get_recstatus() == "STOPPING":
            logging.info("Record is paused/stopped, not checking file length etc. - avoiding race condition")
            return True
        #fs1 = os.path.getsize(self.RECDIR + "/" + TIKCFG['capture']['src1_fn_orig'])
        #time.sleep(2)
        #if os.path.getsize(self.RECDIR + "/" + TIKCFG['capture']['src1_fn_orig']) == fs1:
        #todo: if needed, reimplement
        if False:
            logging.error("File size does not change. Restarting pipeline...")
            self.retrycount += 1
            del self.pipes['src1']
            try:
                del self.pipes['src2']
            except:
                pass

            self.pipe_create()

            restarttimer = threading.Timer(1.0, self.record_start)
            restarttimer.start()
        else:
            logging.info("Recording started, pipeline is running. Everything is fine.")
            self.set_recstatus("RECORDING")
            self.set_ocstatus("capturing")
            self.recordingsince = datetime.datetime.now()


    def record_start(self):
        logging.info("Starting pipelines...")
        # restart pipeline if we're paused
        if self.get_pipestatus() == "paused":
            logging.info("Restarting from pause")
            logging.debug("Starting pipe1")
            self.pipes['src1'].set_state(Gst.State.PLAYING)
            try:
                logging.debug("Starting pipe2")
                self.pipes['src2'].set_state(Gst.State.PLAYING)
            except NameError:
                logging.debug("No pipe2 defined")
            self.set_recstatus("RECORDING")

            return True

        else:
            # start new pipeline if there's a new recording
            if self.pipe_create():
                logging.debug("Pipe(s) created. Setting pipeline(s) to PLAYING.")
                self.pipes['src1'].set_state(Gst.State.PLAYING)
                try:
                    self.pipes['src2'].set_state(Gst.State.PLAYING)
                except NameError:
                    logging.debug("No pipe2 defined, skipping...")

                logging.info("Recording started, pipeline is running. Everything is fine.")
                self.set_recstatus("RECORDING")
                self.set_ocstatus("capturing")

                self.recordingsince = datetime.datetime.now()



    def record_stop(self, eos=False):
        logging.info("Stopping pipeline...")
        self.set_recstatus("STOPPING")

        # send EOS to make files complete
        if eos:
            try:
                self.pipes['src1'].send_event(Gst.Event.new_eos())
                self.pipes['src2'].send_event(Gst.Event.new_eos())
            except:
                #todo should we catch more specific errors here? It might be okay to just catch all.
                pass

            # waits for the message that EOS is written - "hangs" on purpose!
            logging.debug("Waiting 5 s for EOS")
            b = self.buses['src1'].timed_pop_filtered(5*1000*1000*1000, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            logging.debug("EOS written")

        try:
            logging.debug("GST pipe1 says: %s" % self.pipes['src1'].set_state(Gst.State.NULL))
            logging.debug("GST pipe2 says: %s" % self.pipes['src2'].set_state(Gst.State.NULL))
            del self.pipes['src1']
            del self.pipes['src2']
        except:
            #todo see above
            pass

        self.set_recstatus("STOPPED")
        self.set_recstatus("IDLE")
        self.set_ocstatus("idle")

        logging.info("Pipeline stopped and removed.")


        logging.debug("List and length of files in recording dir '%s':"%self.RECDIR)
        fns = os.listdir(self.RECDIR)
        for fn in fns:
            logging.debug("%s, %s"%(fn, os.path.getsize(self.RECDIR + "/" + fn)))
        # write the state into the directory
        self.recordingsince = None
        

    def record_pause(self):
        logging.info("Pausing pipeline...")
        self.set_recstatus("PAUSING")
        logging.debug("GST pipe1 says: %s" % self.pipes['src1'].set_state(Gst.State.PAUSED))
        try:
            logging.debug("GST pipe2 says: %s" % self.pipes['src2'].set_state(Gst.State.PAUSED))
        except NameError:
            logging.debug("No GST pipe2 defined, so only pausing pipe1")
        self.set_recstatus("PAUSED")
        logging.info("Pipeline paused.")


    def get_pipestatus(self):
        # Return the GST status of pipe1, which usually can be PLAYING or PAUSED, and "translate" to OC states.
        try:
            curstate = self.pipes['src1'].get_state(False)[1]
            logging.debug("GST was asked for pipe status. Answer: %s"%curstate)
            if curstate == Gst.State.PLAYING:
                return "capturing"
            elif curstate == Gst.State.PAUSED:
                return "paused"
            else:
                return "stopped"
        except (NameError, AttributeError):
            logging.debug("There is no pipeline to be asked for status.")
            return "stopped"

    def setup_recorddir(self, subdir=None, epidata=None, props=None):
        # create the directory for our recording.
        # if it's scheduled, it has a meaningful name consisting of the WFID and the date
        # otherwise, it's just date and "Unscheduled"
        if subdir == None:
            # create subdir from date and time
            self.RECDIR = TIKCFG['capture']['directory'] + "/" + datetime.datetime.now().strftime("Unscheduled_%Y%m%d_%H%M%S")
        else:
            self.RECDIR = TIKCFG['capture']['directory'] + "/" + subdir + datetime.datetime.now().strftime("_%Y%m%d_%H%M%S")

        logging.debug("Trying to create recording directory '%s'..."%self.RECDIR)
        try:
            os.makedirs(self.RECDIR)
            logging.debug("Creating RecDir '%s': Success."%self.RECDIR)
        except FileExistsError:
            self.RECDIR = self.RECDIR + "_" + str(time.time()).split(".")[1]
            logging.info("Recording dir already exists. Generating unique dirname '%s'."%self.RECDIR)
            try:
                os.makedirs(self.RECDIR)
            except:
                logging.error("Could not create dir %s! Check write permissions! Setting up dir in /tmp instead."%self.RECDIR)
                self.RECDIR = "/tmp/" + self.RECDIR
                os.makedirs(self.RECDIR)

        # save episode.xml

        if not epidata == None:
            with open(self.RECDIR + "/" + "episode.xml", "w") as f:
                logging.debug("Writing episode.xml...")
                f.write(epidata)
            ingester.write_dirstate(self.RECDIR, "WFIID\t%s"%self.NEXTWFIID)

        # save recording.properties
        if not props == None:
            with open(self.RECDIR + "/" + "recording.properties", "w") as f:
                f.write(props)
                logging.debug("Writing recording.properties...")
        # todo: auch series.xml?

    def write_line_states(self, statestr):
        if self.get_recstatus() == "RECORDING":
            fn = self.RECDIR + "/state.log"
            ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            logging.debug("Saving signal line states.")
            with open(fn, 'a') as f:
                f.write("%s,%s,%s\n"%
                        (datetime.datetime.now().strftime("%Y%m%d%H%M%S"), statestr[0], statestr[1]))

    def set_ocstatus(self, status):
        statlist = ["idle", "capturing", "error", "manifest", "uploading", "upload_finished"]
        if status in statlist:
            self.OCSTATE = status
            ingester.set_oc_castate(status)
            return True
        else:
            logging.debug("OC state '%s' does not exist and will not be set."%status)
            return False

    def set_recstatus(self, status):
        statlist = ["RECORDING", "STARTING", "ERROR", "IDLE", "PAUSED", "PAUSING", "STOPPING", "RECORDED"]
        if status in statlist:
            self.RECSTATE = status
            if self.RECDIR != None:
                ingester.write_dirstate(self.RECDIR, status)
            return True
        else:
            logging.debug("Record state '%s' cannot be set."%status)
            return False

    def get_ocstatus(self):
        return self.OCSTATE

    def get_recstatus(self):
        return self.RECSTATE









# für die CAs:
# GST_DEBUG=2 gst-launch-1.0 -v  udpsrc multicast-group=239.25.1.1 port=4444  multicast-iface=eth0 !  'video/mpegts, systemstream=(boolean)true, packetsize=(int)188' !  filesink location=test.h264
# In zwei Files:
# gst-launch-1.0 -v  udpsrc multicast-group=239.25.1.1 port=4444  multicast-iface=eth0 !  'video/mpegts, systemstream=(boolean)true, packetsize=(int)188' ! tsdemux name=dem ! queue ! video/x-h264 ! filesink location=test.h264 dem. ! queue ! audio/mpeg ! filesink location=test.aac
# Demuxen und wieder muxen:
# gst-launch-1.0 -v -e mpegtsmux name="muxer" ! filesink location="bla.mts" \
# udpsrc multicast-group=239.25.1.1 port=4444  multicast-iface=eth0 ! 'video/mpegts, systemstream=(boolean)true, packetsize=(int)188' ! tsdemux name=dem \
# ! queue ! audio/mpeg ! muxer. \
# dem. ! video/x-h264 ! queue ! muxer.
# mit Matroska:
# gst-launch-1.0 -v matroskamux name="muxer" ! filesink location="bla.mp4" sync=false udpsrc multicast-group=239.25.1.1 port=4444 \
#  multicast-iface=eth0 ! 'video/mpegts, systemstream=(boolean)true, packetsize=(int)188' ! tsdemux name=dem ! \
# queue ! video/x-h264 ! h264parse !  muxer. dem. ! queue ! audio/mpeg ! aacparse ! muxer.

# Kamera:
# gst-launch-1.0 -e rtspsrc location=rtsp://172.25.111.3/ssm/video1 latency=0 name=demux demux. ! queue ! rtph264depay  ! h264parse !  mp4mux name=mux ! filesink location=video.out demux. ! queue ! rtpmp4gdepay ! aacparse ! mux.
# oder gst-launch-1.0 -e rtspsrc location=rtsp://172.25.111.3/video1 latency=0 name=demux demux. ! queue ! rtph264depay  ! h264parse !  mp4mux name=mux ! filesink location=video.out demux. ! queue ! rtpmp4gdepay ! aacparse ! audio/aac !  mux.
