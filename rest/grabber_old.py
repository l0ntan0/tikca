#!/usr/bin/python3
import gi
import time
import os
from configobj import ConfigObj
import datetime
import threading
from gi.repository import GObject, Gst
gi.require_version('Gst', '1.0')
GObject.threads_init()
Gst.init(None)
import sys
from pyca import ca
import logging
import subprocess
import ingester

os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))
#TODO: Check whether config files are there, and maybe include option to give config files in command line

CONFIG = ca.update_configuration('/etc/pyca.conf')
TIKCONFIG = ConfigObj('/etc/tikca.conf')

class Grabber:
    global TIKCONFIG
    global CONFIG
    def __init__(self):
        logging.info("STARTING TIKCA")
        self.stoprequest = threading.Event()
        self.set_recstatus("IDLE")
        self.set_ocstatus("idle")
        self.RECDIR = "."
        self.retrycount = 0
        self.recordingsince = None

        self.init_pipe()
        
        logging.info("Grabber initialized")

    def init_pipe(self):
        # Create GStreamer pipeline
        # TODO: only do this if state != playing
        logging.debug("Creating GStreamer Pipeline")
        self.pipeline = Gst.Pipeline()

        # Create bus to get events from GStreamer pipeline
        self.bus = self.pipeline.get_bus()

        self.bus.add_signal_watch()
        self.bus.connect('message::error', self.on_error)
        self.bus.connect('message::pad_added', self.on_pad_added)

        # This is needed to make the video output in our DrawingArea:
        #self.bus.enable_sync_message_emission()



    def standby(self):
        logging.debug("Setting up pipeline")
        # TODO: only do this if state != playing
        # Key to happiness for demuxing one stream:
        # gst-launch-1.0 -e udpsrc uri=udp://239.25.1.34:4444 ! video/mpegts, systemstream=\(boolean\)true, packetsize=\(int\)188 !
        # tsdemux name=d ! queue ! video/x-h264 ! filesink location="out.h264" d. ! queue ! audio/mpeg, mpegversion=\(int\)2,
        # stream-format=\(string\)adts ! filesink location="out.aac"

        # set up recording dir and file names
        fn_vid1 = self.RECDIR + "/" + TIKCONFIG['capture']['src1_fn_vid']
        fn_aud1 = self.RECDIR + "/" + TIKCONFIG['capture']['src1_fn_aud']

        # needed to filter the MPEGTS stream from ENC-300 encoders
        self.caps = Gst.caps_from_string('video/mpegts, systemstream=(boolean)true, packetsize=(int)188')
        self.audiocaps = Gst.caps_from_string("audio/mpeg, mpegversion=(int)2, stream-format=(string)adts")
        self.videocaps = Gst.caps_from_string("video/x-h264")

        prot_src1 = TIKCONFIG['capture']['src1_uri'].split("://")[0]
        if prot_src1 == "udp":
            # set up elements: src -> caps -> demux -> caps -> sinks
            self.src1 = Gst.ElementFactory.make('udpsrc', "udpsrc1")
            self.src1.set_property('multicast-iface', TIKCONFIG['capture']['src1_iface'])
            self.src1.set_property('buffer-size', 0)
            if len(TIKCONFIG['capture']['src1_uri']) > 15:
                self.src1.set_property('uri', TIKCONFIG['capture']['src1_uri'])
            else:
                self.src1.set_property('port', int(TIKCONFIG['capture']['src1_uri'].split("@")[-1]))



            self.pipeline.add(self.src1)

            self.capsFilter1 = Gst.ElementFactory.make("capsfilter", None)
            self.capsFilter1.props.caps = self.caps
            self.pipeline.add(self.capsFilter1)

            self.audiocapsfilter1 = Gst.ElementFactory.make("capsfilter", None)
            self.audiocapsfilter1.props.caps = self.audiocaps
            self.pipeline.add(self.audiocapsfilter1)

            self.videocapsfilter1 = Gst.ElementFactory.make("capsfilter", None)
            self.videocapsfilter1.props.caps = self.videocaps
            self.pipeline.add(self.videocapsfilter1)

            self.queue_aud1 = Gst.ElementFactory.make('queue', None)
            self.pipeline.add(self.queue_aud1)
            self.queue_vid1 = Gst.ElementFactory.make('queue', None)
            self.pipeline.add(self.queue_vid1)


            self.demux1 = Gst.ElementFactory.make("tsdemux", "d1")
            self.pipeline.add(self.demux1)
            self.demux1.connect("pad-added", self.on_pad_added, [self.queue_aud1, self.queue_vid1])

            self.sink_vid1 = Gst.ElementFactory.make('filesink', None)
            self.sink_vid1.set_property('location', fn_vid1)
            self.sink_vid1.set_property('sync', False)
            self.pipeline.add(self.sink_vid1)

            self.sink_aud1 = Gst.ElementFactory.make('filesink', None)
            self.sink_aud1.set_property('location', fn_aud1)
            self.sink_aud1.set_property('sync', False)
            self.pipeline.add(self.sink_aud1)


            self.h264parse1 = Gst.ElementFactory.make('h264parse', None)
            self.pipeline.add(self.h264parse1)

            self.mux1 = Gst.ElementFactory.make('matroskamux', None)
            self.pipeline.add(self.mux1)

            # link everything
            self.src1.link(self.capsFilter1)
            self.capsFilter1.link(self.demux1)
            # demuxer is linked dynamically to queue_aud and _vid
            self.queue_aud1.link(self.audiocapsfilter1)
            self.audiocapsfilter1.link(self.sink_aud1)
            self.queue_vid1.link(self.videocapsfilter1)
            self.videocapsfilter1.link(self.h264parse1)
            self.h264parse1.link(self.mux1)
            self.mux1.link(self.sink_vid1)

        elif prot_src1 == "rtsp":

            # set up elements: src -> caps -> demux -> caps -> sinks
            self.src1 = Gst.ElementFactory.make('rtspsrc', "rtspsrc1")
            self.src1.set_property('location', TIKCONFIG['capture']['src1_uri'])
            self.src1.set_property('latency', 0)


            self.pipeline.add(self.src1)

            self.queue_aud1 = Gst.ElementFactory.make('queue', None)
            self.pipeline.add(self.queue_aud1)
            self.queue_vid1 = Gst.ElementFactory.make('queue', None)
            self.pipeline.add(self.queue_vid1)

            self.src1.connect("pad-added", self.on_pad_added, [self.queue_aud1, self.queue_vid1])

            self.rtph264depay1 = Gst.ElementFactory.make('rtph264depay', None)
            self.pipeline.add(self.rtph264depay1)

            self.h264parse1 = Gst.ElementFactory.make('h264parse', None)
            self.pipeline.add(self.h264parse1)

            self.mux1 = Gst.ElementFactory.make('matroskamux', None)
            self.pipeline.add(self.mux1)

            self.sink_vid1 = Gst.ElementFactory.make('filesink', None)
            self.sink_vid1.set_property('location', fn_vid1)
            self.sink_vid1.set_property('sync', False)
            self.pipeline.add(self.sink_vid1)

            self.rtpmp4gdepay1 = Gst.ElementFactory.make('rtpmp4gdepay', None)
            self.pipeline.add(self.rtpmp4gdepay1)

            self.aacparse1 = Gst.ElementFactory.make('aacparse', None)
            self.pipeline.add(self.aacparse1)

            self.avdec_aac1 = Gst.ElementFactory.make('avdec_aac', None)
            self.pipeline.add(self.avdec_aac1)

            self.audioconvert1= Gst.ElementFactory.make('audioconvert', None)
            self.pipeline.add(self.audioconvert1)

            self.flacenc1 = Gst.ElementFactory.make('flacenc', None)
            self.pipeline.add(self.flacenc1)


            self.sink_aud1 = Gst.ElementFactory.make('filesink', None)
            self.sink_aud1.set_property('location', fn_aud1)
            self.sink_aud1.set_property('sync', False)
            self.pipeline.add(self.sink_aud1)


            self.queue_vid1.link(self.rtph264depay1)
            self.rtph264depay1.link(self.h264parse1)
            self.h264parse1.link(self.mux1)
            self.mux1.link(self.sink_vid1)

            self.queue_aud1.link(self.rtpmp4gdepay1)
            self.rtpmp4gdepay1.link(self.aacparse1)

            self.aacparse1.link(self.avdec_aac1)
            self.avdec_aac1.link(self.audioconvert1)
            self.audioconvert1.link(self.flacenc1)
            self.flacenc1.link(self.sink_aud1)

        else:
            logging.error("Unsupported protocol for SRC1!")
            #return False

        try:
            TIKCONFIG['capture']['src2_uri']
            fn_vid2 = self.RECDIR + "/" + TIKCONFIG['capture']['src2_fn_vid']
            fn_aud2 = self.RECDIR + "/" + TIKCONFIG['capture']['src2_fn_aud']
            # SRC2 only supports UDP. I'm too lazy.
            # set up elements: src -> caps -> demux -> caps -> sinks

            self.src2 = Gst.ElementFactory.make('udpsrc', "udpsrc2")
            self.src2.set_property('uri', TIKCONFIG['capture']['src2_uri'])
            self.src2.set_property('buffer-size', 0)
            self.src2.set_property('multicast-iface', TIKCONFIG['capture']['src2_iface'])

            self.pipeline.add(self.src2)

            self.capsFilter2 = Gst.ElementFactory.make("capsfilter", None)

            self.capsFilter2.props.caps = self.caps
            self.pipeline.add(self.capsFilter2)

            self.audiocapsfilter2 = Gst.ElementFactory.make("capsfilter", None)
            self.audiocapsfilter2.props.caps = self.audiocaps
            self.pipeline.add(self.audiocapsfilter2)

            self.videocapsfilter2 = Gst.ElementFactory.make("capsfilter", None)
            self.videocapsfilter2.props.caps = self.videocaps
            self.pipeline.add(self.videocapsfilter2)

            self.demux2 = Gst.ElementFactory.make("tsdemux", "d2")

            self.pipeline.add(self.demux2)

            self.sink_vid2 = Gst.ElementFactory.make('filesink', None)
            self.sink_vid2.set_property('location', fn_vid2)
            self.sink_vid2.set_property('sync', False)
            self.pipeline.add(self.sink_vid2)

            self.sink_aud2 = Gst.ElementFactory.make('filesink', None)
            self.sink_aud2.set_property('location', fn_aud2)
            self.sink_aud2.set_property('sync', False)
            self.pipeline.add(self.sink_aud2)

            self.queue_aud2 = Gst.ElementFactory.make('queue', None)
            self.pipeline.add(self.queue_aud2)
            self.queue_vid2 = Gst.ElementFactory.make('queue', None)
            self.pipeline.add(self.queue_vid2)

            self.demux2.connect("pad-added", self.on_pad_added, [self.queue_aud2, self.queue_vid2])

            self.h264parse2 = Gst.ElementFactory.make('h264parse', None)
            self.pipeline.add(self.h264parse2)

            self.mkvmux2 = Gst.ElementFactory.make('matroskamux', None)
            self.pipeline.add(self.mkvmux2)

            # link everything!
            self.src2.link(self.capsFilter2)
            self.capsFilter2.link(self.demux2)
            # demuxer is linked dynamically to queue_aud and _vid
            self.queue_aud2.link(self.audiocapsfilter2)
            self.audiocapsfilter2.link(self.sink_aud2)
            self.queue_vid2.link(self.videocapsfilter2)
            self.videocapsfilter2.link(self.h264parse2)
            self.h264parse2.link(self.mkvmux2)
            self.mkvmux2.link(self.sink_vid2)
        except KeyError:
            logging.debug("No SRC2 defined. Setting up pipeline only for SRC1.")

        return True

    def on_pad_added(self, element, src_pad, q):
        # needed because the demuxer has dynamically added pads
        # here, the pads are selected due to the naming/content

        caps = src_pad.query_caps(None)
        name = caps.to_string()
        logging.debug("Pads added: %s"%(name))


        if name.startswith("application/x-rtp, media=(string)video"):
            sink_pad = q[1].get_static_pad('sink')
            src_pad.link(sink_pad)
            logging.debug("Video queue pad linked (RTP): %s"%sink_pad)

        elif name.startswith("application/x-rtp, media=(string)audio"):
            sink_pad = q[0].get_static_pad('sink')
            src_pad.link(sink_pad)
            logging.debug("Audio queue pad linked (RTP): %s"%sink_pad)

        elif name.startswith("video/x-h264"):
            sink_pad = q[1].get_static_pad('sink')
            src_pad.link(sink_pad)
            logging.debug("Video queue pad linked (MPEGTS): %s"%sink_pad)

        elif name.startswith("audio/mpeg"):
            sink_pad = q[0].get_static_pad('sink')
            src_pad.link(sink_pad)
            logging.debug("Audio queue pad linked (MPEGTS): %s"%sink_pad)

    def checkstarted(self):
        logging.info("Checking whether recording has started or not...")

        variante = 2

        if variante == 1:
            if self.pipeline.get_state(False)[1] == Gst.State.PAUSED or \
                            self.pipeline.get_state(False)[1] == Gst.State.NULL: #\
                    #and self.retrycount < int(TIKCONFIG['capture']['retry']):
                logging.error("Pipeline didn't start. Retrying (%i of %i)..."%
                              (self.retrycount, int(TIKCONFIG['capture']['retry'])))
                del self.pipeline
                self.init_pipe()
                self.standby()
                logging.error("Restarting pipeline...")
                restarttimer = threading.Timer(4.0, self.record_start)
                restarttimer.start()
        elif variante == 2:
            #print(os.path.getsize(self.RECDIR + "/" + TIKCONFIG['capture']['src2_fn_vid']))
            if os.path.getsize(self.RECDIR + "/" + TIKCONFIG['capture']['src2_fn_vid']) == 0:
                logging.error("File size does not change. Restarting pipeline...")
                self.retrycount += 1
                del self.pipeline
                self.init_pipe()
                self.standby()
                restarttimer = threading.Timer(4.0, self.record_start)
                restarttimer.start()
            else:
                logging.info("Recording started! Pipeline is running! Everything is fine.")
                if self.get_pipestatus() == "capturing":
                    self.set_recstatus("RECORDING")
                    self.set_ocstatus("capturing")
                    self.recordingsince = datetime.datetime.now()


    def record_start(self):
        logging.info("Starting pipeline...")
        self.pipeline.set_state(Gst.State.PLAYING)
        self.set_recstatus("STARTING")


        if self.retrycount < int(TIKCONFIG['capture']['retry']):
            failtimer = threading.Timer(4.0, self.checkstarted)
            failtimer.start()
        else:
            self.set_recstatus("ERROR")
            self.set_ocstatus("idle")
            logging.debug("Maximum retry count! Not trying to record anymore.")
            self.retrycount = 0



        """while self.pipeline.get_state(False)[1] == Gst.State.PAUSED \
                and self.retrycount < int(TIKCONFIG['capture']['retry']):
            logging.error("Pipeline didn't start. Retrying (%i of %i)..."%
                          (self.retrycount, int(TIKCONFIG['capture']['retry'])))
            self.retrycount += 1
            #del self.pipeline
            logging.info("Pipeline stopped and removed.")
            self.init_pipe()
            #time.sleep(1)
            self.standby()
            #time.sleep(1)
            logging.info("Starting pipeline...")
            self.pipeline.set_state(Gst.State.PLAYING)
            #time.sleep(int(TIKCONFIG['capture']['retrywait']))

        if self.retrycount >= int(TIKCONFIG['capture']['retry']):
            logging.error("START OF PIPELINE FAILED! CRITICAL ERROR!")
            self.RECSTATE = "ERROR"
            del self.pipeline
            return False

        elif self.pipeline.get_state(False)[1] == Gst.State.PLAYING:
            logging.info("Pipeline started for real - RECORDING!")
            self.RECSTATE = "RECORDING"
            self.OCSTATE = "capturing"
            return True"""


        
    def record_stop(self, eos = True):
        #import inspect
        #logging.debug(inspect.stack()[1][3])
        logging.info("Stopping pipeline...")
        self.set_recstatus("STOPPING")

        # send EOS to make files complete
        if eos:
            self.pipeline.send_event(Gst.Event.new_eos())

            # waits for the message that EOS is written - "hangs" on purpose!
            logging.debug("Waiting for EOS")
            b = self.bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.ERROR | Gst.MessageType.EOS)

            logging.debug("EOS written")

        logging.debug("GST says: %s"%self.pipeline.set_state(Gst.State.NULL))

        self.set_recstatus("IDLE")
        self.set_ocstatus("idle")
        del self.pipeline
        logging.info("Pipeline stopped and removed.")

        self.init_pipe()
        self.retrycount = 0

        logging.debug("List and length of files in recording dir '%s':"%self.RECDIR)
        fns = os.listdir(self.RECDIR)
        for fn in fns:
            logging.debug("%s, %s"%(fn, os.path.getsize(self.RECDIR + "/" + fn)))
        # write the state into the directory
        ingester.write_state(self.RECDIR, "STOPPED")
        

    def record_pause(self):
        logging.info("Pausing pipeline...")
        self.set_recstatus("PAUSING")
        logging.debug("GST says: %s"%self.pipeline.set_state(Gst.State.PAUSED))
        #todo wait for GST to pause
        self.set_recstatus("PAUSED")
        logging.info("Pipeline paused.")

    def quit(self, window):
        self.pipeline.set_state(Gst.State.NULL)

    def on_error(self, bus, msg):
        logging.error("HILFE")
        logging.error('on_error():', msg.parse_error())

    def get_pipestatus(self):
        curstate = self.pipeline.get_state(False)[1]
        logging.debug("GST was asked for pipe status. Answer: %s"%curstate)
        if curstate == Gst.State.PLAYING:
            return "capturing"
        elif curstate == Gst.State.PAUSED:
            return "paused"
        else:
            return "stopped"

    def setup_recorddir(self, subdir=None, epidata=None, props=None):

        if subdir == None:
            # create subdir from date and time
            self.RECDIR = CONFIG['capture']['directory'] + "/" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        else:
            self.RECDIR = CONFIG['capture']['directory'] + "/" + subdir

        logging.debug("Trying to create recording directory '%s'..."%self.RECDIR)
        try:
            os.makedirs(self.RECDIR)
            logging.debug("Creating RecDir: Success.")
        except FileExistsError:
            self.RECDIR = self.RECDIR + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            logging.info("Recording dir already exists. Generating unique dirname '%s'."%self.RECDIR)
            os.makedirs(self.RECDIR)

        # save episode.xml
        if not epidata == None:
            with open(self.RECDIR + "/" + "episode.xml", "w") as f:
                f.write(epidata)
                logging.debug("Writing episode.xml...")

        # save recording.properties
        if not props == None:
            with open(self.RECDIR + "/" + "recording.properties", "w") as f:
                f.write(props)
                logging.debug("Writing recording.properties...")
        # todo: auch series.xml?

    def write_states(self, statestr):
        if self.get_recstatus() == "RECORDING":
            fn = self.RECDIR + "/state.log"
            ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            logging.debug("Saving signal line states.")
            with open(fn, 'a') as f:
                f.write("%s,%s,%s\n"%
                        (datetime.datetime.now().strftime("%Y%m%d%H%M%S"), statestr[0], statestr[1]))

    def set_ocstatus(self, status):
        statlist = ["idle", "uploading", "capturing", "error"]
        if status in statlist:
            self.OCSTATE = status
            return True
        else:
            logging.debug("OC state %s cannot be set"%status)
            return False

    def set_recstatus(self, status):
        statlist = ["RECORDING", "STARTING", "ERROR", "IDLE", "PAUSED", "PAUSING", "STOPPING"]
        if status in statlist:
            self.RECSTATE = status
            return True
        else:
            logging.debug("Record state %s cannot be set"%status)
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


a = """
    def record_setup_DEPRECATED(self, subdir, fake=False):
        # this is deprecated!
        logging.debug("Making pipeline...")
        if fake:
            logging.info("Setting up FAKE sources...")
            self.src1 = Gst.ElementFactory.make('videotestsrc', None)
            self.src2 = Gst.ElementFactory.make('videotestsrc', None)
            self.caps = Gst.caps_from_string('video/x-raw, width=640, height=480')

        else:

            self.src1 = Gst.ElementFactory.make('udpsrc', None)
            logging.info("Setting up source %s"%TIKCONFIG['capture']['src1_uri'])
            self.src1.set_property('uri', TIKCONFIG['capture']['src1_uri'])
            #self.src1.set_property('multicast-group', TIKCONFIG['capture']['SOURCEIP1'])
            #self.src1.set_property('port', int(TIKCONFIG['capture']['SOURCEPORT1']))
            self.src1.set_property('multicast-iface', "eth0")

            self.src2 = Gst.ElementFactory.make('udpsrc', None)
            logging.info("Setting up source %s"%TIKCONFIG['capture']['src2_uri'])
            self.src2.set_property('uri', TIKCONFIG['capture']['src2_uri'])
            #self.src2.set_property('multicast-group', TIKCONFIG['capture']['SOURCEIP2'])
            #self.src2.set_property('port', int(TIKCONFIG['capture']['SOURCEPORT2']))
            self.src2.set_property('multicast-iface', "eth0")

            self.caps = Gst.caps_from_string('video/mpegts, systemstream=(boolean)true, packetsize=(int)188')



        self.pipeline.add(self.src1)
        self.pipeline.add(self.src2)

        self.capsFilter1 = Gst.ElementFactory.make("capsfilter", None)
        self.capsFilter1.props.caps = self.caps
        self.capsFilter2 = Gst.ElementFactory.make("capsfilter", None)
        self.capsFilter2.props.caps = self.caps

        self.pipeline.add(self.capsFilter1)
        self.pipeline.add(self.capsFilter2)


        self.setup_recorddir(subdir)

        fn1 = self.RECDIR + "/" + TIKCONFIG['capture']['src1_outfile']
        fn2 = self.RECDIR + "/" + TIKCONFIG['capture']['src2_outfile']

        logging.debug("Creating file sinks...")
        self.sink1 = Gst.ElementFactory.make('filesink', None)
        self.sink1.set_property('location', fn1)
        self.sink2 = Gst.ElementFactory.make('filesink', None)
        self.sink2.set_property('location', fn2)
        self.pipeline.add(self.sink1)
        self.pipeline.add(self.sink2)

        logging.debug("Linking pipeline...")
        self.src1.link(self.capsFilter1)
        self.src2.link(self.capsFilter2)

        saveformat = "mts"
        if saveformat == "mts":
            self.capsFilter1.link(self.sink1)
            self.capsFilter2.link(self.sink2)

        logging.debug("Done setting up pipeline.")
        return True
          """

#Kamera:
#gst-launch-1.0 -e rtspsrc location=rtsp://172.25.111.3/video1 latency=0 name=demux demux. ! queue ! rtph264depay  ! h264parse !  mp4mux name=mux ! filesink location=video.out demux. ! queue ! rtpmp4gdepay ! aacparse ! audio/aac !  mux.
