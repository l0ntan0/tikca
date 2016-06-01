#!/usr/bin/python3
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
import time
import os
from configobj import ConfigObj
import datetime
import threading
GObject.threads_init()
Gst.init(None)
from pyca import ca
import logging
import subprocess
import shlex
import json
import zipfile
import csv
import argparse
from io import BytesIO as bio
from xml.etree import ElementTree
from xml.dom import minidom
import pycurl

parser = argparse.ArgumentParser()
parser.add_argument("--pycacfg", help="the pyca configfile", default='/etc/pyca.conf')
parser.add_argument("--tikcacfg", help="the tikca configfile", default='/etc/tikca.conf')
args = parser.parse_args()

#print(args.tikcacfg)
CONFIG = ca.update_configuration(args.pycacfg)
TIKCFG = ConfigObj(args.tikcacfg, list_values=True)
caproot = CONFIG['capture']['directory']

class Ingester:
    global TIKCFG
    global CONFIG
    def __init__(self):
        logging.info("STARTING INGESTER SERVICE...")
        self.ingestscanloop()


    def on_pad_added(self, element, src_pad, q):
        # needed because the demuxer has dynamically added pads
        # here, the pads are selected due to the naming/content


        logging.debug("input element %s"%element)
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

    def get_media_length(self, dirname, fn):
        comm = "%s -i %s -print_format json -show_streams"%(TIKCFG['ingester']['probe'], caproot + "/" + dirname + "/" + fn)
        logging.debug(comm)
        args = shlex.split(comm)
        with subprocess.Popen(args,stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as p:
            jsondata = json.loads(p.stdout.read().decode("UTF-8"))
            try:
                return(round(float(jsondata['streams'][0]['duration'])))
            except KeyError:
                logging.error('Did not find any length data in FFProbe output: %s'%jsondata)
                #todo irgendeine andere zeit generieren
                return(0)



    def analyze_stats(self, dirname):
        # B is for black, P for presentation, C for camera
        logging.debug("Analyzing directory '%s'"%dirname)
        fns = []
        flavors = []
        try:
            with open(caproot + "/" + dirname + "/" + "state.log", 'r') as f:
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
                fns.append(TIKCFG['capture']['src1_fn_vid'])
                if src1_max == "C":
                    src1_flavor = "presenter/source"
                else:   # if max = P or max = B
                    src1_flavor = "presentation/source"


            if src2_stats["C"] + src2_stats["P"] - src2_stats["B"] < 10:
                src2_flavor = None
            else:
                try:
                    TIKCFG['capture']['src2_fn_vid']
                    fns.append(TIKCFG['capture']['src2_fn_vid'])
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
                fns.append(TIKCFG['capture']['src1_fn_aud'])
            else:
                try:
                    TIKCFG['capture']['src2_fn_aud']
                    fns.append(TIKCFG['capture']['src2_fn_aud'])
                except KeyError:
                    # send the SRC1 audio file even if it is "black", so we've got something to send
                    fns.append(TIKCFG['capture']['src1_fn_aud'])
            flavors.append("presenter-audio/source")


        except FileNotFoundError:
            logging.error("No log file in directory %s! Using default flavor names."%dirname)

            stdfnsflvs = {TIKCFG['capture']['src1_fn_aud']: TIKCFG['capture']['src1_stdflavor'],
                      TIKCFG['capture']['src1_fn_vid']: TIKCFG['capture']['stdflavor_audio'],
                      TIKCFG['capture']['src2_fn_aud']: "presenter/backup",
                      TIKCFG['capture']['src2_fn_vid']: TIKCFG['capture']['src1_stdflavor']}

            for tpl in stdfnsflvs:
                if os.path.isfile(caproot + "/" + dirname + "/" + tpl[0]):
                    fns.append(tpl[0])
                    flavors.append(tpl[1])

        logging.debug("File list: %s"%zip(flavors, fns))

        with open(caproot + "/" + dirname + "/.ANA", "w") as anafile:
            anafile.write(json.dumps((flavors, fns)))

        return (flavors, fns)

            # OLD STUFF
            # Attention! In Python2 (in which ca.py is programmed), zip() returns a iteratable list.
            # In Python3 though, this returns an iterator. So we need to fix this by doing list(zip()).
            # return list(zip(flavors, fns))

    def curlreq(self, url, post_data=None, ocmode=True, showprogress=False):

        buf = bio()
        curl = pycurl.Curl()
        curl.setopt(curl.URL, url.encode('ascii', 'ignore'))

        if post_data:
            curl.setopt(curl.HTTPPOST, post_data)
        curl.setopt(curl.WRITEFUNCTION, buf.write)
        if ocmode:
            curl.setopt(pycurl.HTTPAUTH, pycurl.HTTPAUTH_DIGEST)
            curl.setopt(pycurl.USERPWD, "%s:%s" % \
                    (CONFIG['server']['username'], CONFIG['server']['password']))
            curl.setopt(curl.HTTPHEADER, ['X-Requested-Auth: Digest'])

        # activate progress function
        if showprogress:

            self.progress_called = 0
            logging.debug("Showing progress of upload...")
            curl.setopt(curl.NOPROGRESS, False)
            curl.setopt(curl.XFERINFOFUNCTION, self.progress)
        xferstart = datetime.datetime.now()
        curl.perform()
        if showprogress:
            logging.info('Transfer duration: {}'.format(datetime.datetime.now() - xferstart))
        status = curl.getinfo(pycurl.HTTP_CODE)
        curl.close()
        if int(status / 100) != 2:
            raise Exception('ERROR: Request to %s failed (HTTP status code %i)' % \
                    (url, status))
        result = buf.getvalue()

        buf.close()
        return result


    def progress(self, download_t, download_d, upload_t, upload_d):
        self.progress_called += 1
        if upload_t > 0 and self.progress_called % 10000 == 0:
            logging.debug("Uploaded {0} % of {1} MB.".format(round(upload_d/upload_t*100, 1), round(upload_t/1024/1024)))

    def get_instancedata(self, wfiid):
        # get instance data from wfiid
        url = "%s/workflow/instance/%s.json"%(CONFIG['server']['url'], wfiid)
        #logging.debug(url)
        #try:
        jsonstring = self.curlreq(url).decode("UTF-8")
        #except Exception:
        #   return json.loads("{}")

        jsondata = json.loads(jsonstring)
        try:
            retdict = {'title': jsondata['workflow']['mediapackage']['title'],
                                    'seriesid': jsondata['workflow']['mediapackage']['series'],
                                    'duration': round(float(jsondata['workflow']['mediapackage']['duration'])),
                                    'start': jsondata['workflow']['mediapackage']['start']}
        except KeyError:
            logging.error("KeyError while fetching %s: %s"%(url, jsondata))
            retdict = dict()
        return retdict
        # id, title, series, creator,

    def zipdir(self, dirname, fns):
        # zip contents of dir to ingest it
        # but only if there's enough space on HD:

        # calc total filesize in dir
        proj_fs = 0
        for fn in fns:
            proj_fs += os.path.getsize(caproot + "/" + dirname + "/" + fn)

        if self.df() < proj_fs/1024/1024:
            logging.error("Not zipping dir %s because there are only %s MB free on device (needed: %s MB)."%(self.df(), round(proj_fs/1024/1024)))
            return False

        zfn = caproot + "/" + dirname +  '/ingest.zip'
        logging.debug("Starting zip process now.")
        with zipfile.ZipFile(zfn, 'w') as ingestzip:
            ingestzip.write(caproot + '/' + dirname +  '/manifest.xml')
            ingestzip.write(caproot + '/' + dirname +  '/series.xml')
            ingestzip.write(caproot + '/' + dirname +  '/episode.xml')
            try:
                ingestzip.write(caproot + "/" + dirname +  '/state.log')
            except:
                logging.info("No state.log file found, not adding to zip file.")
            for fn in fns:
                logging.debug("Adding file %s to zip file %s."%(fn, zfn))
                ingestzip.write(caproot + "/" + dirname +  "/" + fn)
        return True

    def get_wfiid_from_dir(self, subdir):
        states = self.read_statefile(subdir)
        if states[0][1] == "WFIID":
            logging.debug("Found WFIID %s in dir %s"%(states[0][2], subdir))
            return states[0][2]
        else:
            return None

    def get_start_from_dir(self, subdir):
        states = self.read_statefile(subdir)
        for stateline in states:
            if stateline[1] == "STARTED":
                return stateline[0]

        return None

    def ingestscanloop(self, scanroot = caproot):
        # list dirs
        while True:
            dirlist = []
            for entry in os.listdir(scanroot):
                if not entry.startswith('.') and not entry == 'lost+found' and os.path.isdir(scanroot + "/" + entry):
                    dirlist.append(entry)
            self.queue = []

            for dirtoscan in dirlist:
                # open status of recording in directory
                logging.info("Scanning dir %s..."%dirtoscan)
                ls = self.get_last_state(dirtoscan)
                if len(ls) > 1:
                    logging.debug("Last state in %s: %s"%(dirtoscan, ls))
                else:
                    logging.info("No directory information here. Putting RECORDED in .RECSTATUS.")
                    self.write_dirstate(dirtoscan, "RECORDED")
                    ls = self.get_last_state(dirtoscan)

                print(ls)
                self.queue.append((dirtoscan, ls[0], ls[1]))

            #logging.debug("Queue is: %s"%self.queue)
            for task in self.queue:
                wfiid = self.get_wfiid_from_dir(task[0])
                if task[2] == "STOPPED":
                    logging.info("Directory %s contains recorded files. Starting split step."%task[0])
                    # todo: in einen extrathread packen
                    self.write_dirstate(task[0], "SPLITTING")
                    if self.splitfile(task[0], TIKCFG['capture']['src1_fn_orig'], TIKCFG['capture']['src1_fn_vid'],
                                       TIKCFG['capture']['src1_fn_aud']):
                        self.write_dirstate(task[0], "SPLIT\tSTREAM1")
                    else:
                        self.write_dirstate(task[0], "ERROR")

                    # only attempt to split the second file if there is a name defined
                    if len(TIKCFG['capture']['src2_fn_orig']) > 1:
                        if self.splitfile(task[0], TIKCFG['capture']['src2_fn_orig'],
                                        TIKCFG['capture']['src2_fn_vid'], TIKCFG['capture']['src2_fn_aud']):
                            self.write_dirstate(task[0], "SPLIT\tSTREAM2")
                            self.write_dirstate(task[0], "SPLITCOMPLETE")
                        else:
                            self.write_dirstate(task[0], "ERROR")

                elif task[2] == "SPLITCOMPLETE":
                    logging.info("Directory %s contains split files. Starting analyze step."%task[0])
                    self.write_dirstate(task[0], "ANALYZING")
                    flavors, fns = self.analyze_stats(task[0])
                    flength = self.get_media_length(task[0], TIKCFG['capture']['src1_fn_orig'])
                    start = self.get_start_from_dir(task[0])
                    if len(fns) > 0 and \
                        self.write_manifest(dirname=task[0], wfiid=wfiid, duration=flength, start=start, fns=fns, flavors=flavors):
                        self.write_dirstate(task[0], "ANALYZED")
                    else:
                        self.write_dirstate(task[0], "ERROR")
                elif task[2] == "ANALYZED":
                    logging.info("Directory %s is analyzed. Starting upload step."%task[0])
                    self.write_dirstate(task[0], "UPLOADING")
                    if not wfiid == None:
                        instancedict = self.get_instancedata(wfiid)
                        print(instancedict)
                        seriesxml = self.get_seriesdata(instancedict['seriesid'])
                        episodexml = self.get_episodedata(wfiid)
                        self.write_seriesxml(task[0], seriesxml)
                        self.write_episodexml(task[0], episodexml)
                    else:
                        #todo was tut er denn, wenn er keine wfiid hat?
                        pass

                    with open(caproot + "/" + task[0] + "/.ANA", "r") as anafile:
                        jsoncontent = anafile.read()
                        flavors, fns = json.loads(jsoncontent)

                    if self.ingest(fns, flavors, subdir=task[0], wfiid=wfiid):
                        self.write_dirstate(task[0], "UPLOADED")
                    #if self.zipdir(dirname=task[0], fns=fns):
                    #    self.write_dirstate(task[0], "ZIPPED")
                    #else:
                    #    self.write_dirstate(task[0], "ERROR")
                elif task[2] == "ZIPPED":
                    logging.info("Directory %s has already been zipped. Starting ingest step."%task[0])
                    self.write_dirstate(task[0], "UPLOADING")
                    if self.ingest_zipped(task[0]):
                        self.write_dirstate(task[0], "UPLOADED")
                    else:
                        self.write_dirstate(task[0], "ERROR")
                    #todo gucken, dass gefailte/gestoppte ingests (zeit liegt zu lange zurück) neu gestartet werden
                elif task[2] == "UPLOADED":
                    logging.debug("Directory %s has already been ingested, skipping..."%task[0])
                elif task[2] == "ERROR":
                    step_before_error = self.get_last_state(dirtoscan, -3)
                    logging.info("Found dir %s in an error state. Resuming from step '%s'."%(task[0], step_before_error))
                    #todo resume vernünftig einbauen

                elif "ING" in task[2]:
                    logging.debug("Dir %s has work in progress (%s). Not starting anything new."%(task[0], task[2]))

            logging.info("Waiting for %s min(s) to scan again."%TIKCFG['ingester']['looptime'])
            time.sleep(float(TIKCFG['ingester']['looptime']) * 60)

    def get_wfcfg(self, wfiid):
        # this is currently not being used.

        param = []
        url = "%s/instance/%s.json"%(CONFIG['server']['url'], wfiid)
        confjson = json.loads(self.curlreq(url))

        for prop in confjson['workflow']['configurations']:
            param.append((prop['key'], prop['$']))
        return param


    def get_agentprops(self, wfiid):

        # this code snippet is partly stolen from Lars Kiesow's pyCA

        param = []
        wdef = TIKCFG['unscheduled']['workflow']

        if len(wfiid) > 1:
            url = "%s/recordings/%s/agent.properties"%(CONFIG['server']['url'], wfiid)
            properties = self.curlreq(url).decode('utf-8')
            print(properties)

            for prop in properties.split('\n'):
                if prop.startswith('org.opencastproject.workflow.config'):
                    key, val = prop.split('=', 1)
                    key = key.split('.')[-1]
                    param.append((key, val))
                elif prop.startswith('org.opencastproject.workflow.definition'):
                    wdef = prop.split('=', 1)[-1]
            return wdef, param


    def ingest(self, fns, flavors, subdir, wfiid, wfdef=TIKCFG['unscheduled']['workflow']):
    # this code snippet is stolen from Lars Kiesow's pyCA.

        logging.info('Creating new mediapackage')
        mediapackage = self.curlreq('%s/ingest/createMediaPackage'%CONFIG['server']['url'])
        logging.debug("Mediapackage creation answer: %s"%mediapackage.decode("UTF-8"))
        recording_dir = caproot + "/" + subdir

        # add episode DublinCore catalog
        if os.path.isfile('%s/episode.xml' % recording_dir):
            logging.info('Uploading episode DC catalog')
            dublincore = ''
            with open('%s/episode.xml' % recording_dir, 'r') as episodefile:
                dublincore = episodefile.read().encode('utf8', 'ignore')
                # falls das seltsame Umlaute erzeugt, lieber wieder 'rb' als open mode nehmen und das ...encode raus
            fields = [('mediaPackage', mediapackage),
                      ('flavor', 'dublincore/episode'),
                      ('dublinCore', dublincore)]
            mediapackage = self.curlreq('%s/ingest/addDCCatalog'%CONFIG['server']['url'], list(fields))

        # add series DublinCore catalog
        if os.path.isfile('%s/series.xml' % recording_dir):
            logging.info('Uploading series DC catalog')
            dublincore = ''
            with open('%s/series.xml' % recording_dir, 'r') as seriesfile:
                dublincore = seriesfile.read().encode('utf8', 'ignore')
            fields = [('mediaPackage', mediapackage),
                      ('flavor', 'dublincore/series'),
                      ('dublinCore', dublincore)]
            mediapackage = self.curlreq('%s/ingest/addDCCatalog'%CONFIG['server']['url'], list(fields))

        # add track(s)
        tpls = zip(fns, flavors)
        for tpl in tpls:
            logging.info("Uploading file %s (flavor %s)"%(tpl[0], tpl[1]))
            fullfn = caproot + "/" + subdir + "/" + tpl[0]
            track = fullfn.encode('ascii', 'ignore')
            fields = [('mediaPackage', mediapackage), ('flavor', tpl[1]),
                      ('BODY1', (pycurl.FORM_FILE, track))]
            mediapackage = self.curlreq('%s/ingest/addTrack'%CONFIG['server']['url'], list(fields), showprogress=True)

        # ingest
        wfdef, wfcfg = self.get_agentprops(wfiid)

        logging.info('Finishing ingest by writing mediapackage and workflow config')
        fields = [('mediaPackage', mediapackage),
                  ('workflowDefinitionId', wfdef),
                  ('workflowInstanceId', wfiid.encode('ascii', 'ignore'))]
        fields += wfcfg

        mediapackage = self.curlreq('%s/ingest/ingest'%CONFIG['server']['url'], fields, showprogress=True)
        logging.debug(mediapackage)
        logging.info("Finished ingest of WFIID %s (dir '%s')"%(wfiid, subdir))

        return True




    def df(self, mode='mb'):
        statvfs = os.statvfs(CONFIG['capture']['directory'])
        MB_free = round(statvfs.f_frsize * statvfs.f_bavail/1024/1024)
        #logging.debug("MB available: %i"%MB_free)
        return MB_free

    def splitfile(self, dirname, infile, of_vid, of_aud):
        # split mpegts streams into parts

        if not os.path.isfile(caproot + "/" + dirname + "/" + infile):
            logging.error("Error while splitting file: File does not exist ('%s')"%(caproot + "/" + dirname + "/" + infile))
            return False
        fsize = os.path.getsize(caproot + "/" + dirname + "/" + infile)

        if self.df() < 1.5*fsize/1024/1024:
            logging.error("Not enough disk space! (%s MB free, %s MB needed)"%(self.df(), fsize/1024/1024))
            return False
        self.pipeline = Gst.Pipeline()

        # Create bus to get events from GStreamer pipeline
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message::pad_added', self.on_pad_added)

        logging.info("Splitting file %s (%i KB) in dir %s."%(infile, int(fsize/1024), dirname))

        # needed to filter the MPEGTS stream from ENC-300 encoders
        self.caps = Gst.caps_from_string('video/mpegts, systemstream=(boolean)true, packetsize=(int)188')
        self.audiocaps = Gst.caps_from_string("audio/mpeg, mpegversion=(int)2, stream-format=(string)adts")
        self.videocaps = Gst.caps_from_string("video/x-h264")

        # set up elements: src -> caps -> demux -> caps -> sinks
        self.src1 = Gst.ElementFactory.make('filesrc', "filesrc0")

        self.src1.set_property("location", caproot + "/" + dirname + "/" + infile)
        self.pipeline.add(self.src1)

        self.tsparse = Gst.ElementFactory.make("tsparse","tsparser")
        self.pipeline.add(self.tsparse)

        self.capsFilter1 = Gst.ElementFactory.make("capsfilter", "videostreamfilter")
        self.capsFilter1.props.caps = self.caps
        self.pipeline.add(self.capsFilter1)

        self.audiocapsfilter1 = Gst.ElementFactory.make("capsfilter", "audiofilter")
        self.audiocapsfilter1.props.caps = self.audiocaps
        self.pipeline.add(self.audiocapsfilter1)

        self.videocapsfilter1 = Gst.ElementFactory.make("capsfilter", "videofilter")
        self.videocapsfilter1.props.caps = self.videocaps
        self.pipeline.add(self.videocapsfilter1)

        self.queue_aud1 = Gst.ElementFactory.make('queue', "audioqueue")
        self.queue_aud1.set_property("max-size-buffers",0)
        self.queue_aud1.set_property("max-size-time",0)
        self.pipeline.add(self.queue_aud1)

        self.queue_vid1 = Gst.ElementFactory.make('queue', "videoqueue")
        self.queue_vid1.set_property("max-size-buffers",0)
        self.queue_vid1.set_property("max-size-time",0)

        self.pipeline.add(self.queue_vid1)


        self.demux1 = Gst.ElementFactory.make("tsdemux", "d1")
        self.demux1.set_property('emit-stats',True)
        self.pipeline.add(self.demux1)
        self.demux1.connect("pad-added", self.on_pad_added, [self.queue_aud1, self.queue_vid1])

        self.sink_vid1 = Gst.ElementFactory.make('filesink', None)
        self.sink_vid1.set_property('location', caproot + "/" + dirname + "/" + of_vid)
        self.sink_vid1.set_property('sync', False)
        self.pipeline.add(self.sink_vid1)

        self.sink_aud1 = Gst.ElementFactory.make('filesink', None)
        self.sink_aud1.set_property('location', caproot + "/" + dirname + "/" + of_aud)
        self.sink_aud1.set_property('sync', False)
        self.pipeline.add(self.sink_aud1)


        self.mux1 = Gst.ElementFactory.make('matroskamux', None)
        self.pipeline.add(self.mux1)


        self.queue_h264 = Gst.ElementFactory.make('queue', None)
        self.queue_h264.set_property("max-size-buffers",0)
        self.queue_h264.set_property("max-size-time",0)
        self.pipeline.add(self.queue_h264)
        self.h264parse1 = Gst.ElementFactory.make('h264parse', None)
#        self.h264parse1.set_property('config-interval', 10)
        #self.h264parse1.connect("pad-added", self.on_pad_added, [self.mux1])
        self.pipeline.add(self.h264parse1)
        

        # link everything
        self.src1.link(self.tsparse)
        self.tsparse.link(self.capsFilter1)
        self.capsFilter1.link(self.demux1)
        # demuxer is linked dynamically to queue_aud and _vid
        self.queue_aud1.link(self.audiocapsfilter1)
        self.audiocapsfilter1.link(self.sink_aud1)
        self.queue_vid1.link(self.videocapsfilter1)
        self.videocapsfilter1.link(self.queue_h264)
        self.queue_h264.link(self.h264parse1)
        self.h264parse1.link(self.mux1)
        self.mux1.link(self.sink_vid1)
        #self.videocapsfilter1.link(self.sink_vid1)

        bus = self.pipeline.get_bus()
        logging.debug("Setting GST pipeline to 'playing' - start of splitting process.")
        self.pipeline.set_state(Gst.State.PLAYING)

        while True:
            message = bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE,
                                             Gst.MessageType.EOS | Gst.MessageType.ERROR | Gst.MessageType.STREAM_STATUS)
            if message:
                if message.type == Gst.MessageType.ERROR:
                    err, debug = message.parse_error()
                    logging.error("Error received from element %s: %s" % (
                        message.src.get_name(), err))
                    logging.error("Debugging information: %s" % debug)
                    break
                elif message.type == Gst.MessageType.EOS:
                    logging.info("End-Of-Stream reached. Stopping pipeline.")
                    self.pipeline.set_state(Gst.State.PAUSED)
                    del self.pipeline
                    return True
                    break
                elif message.type == Gst.MessageType.STATE_CHANGED:
                    if isinstance(message.src, Gst.Pipeline):
                        old_state, new_state, pending_state = message.parse_state_changed()
                        logging.debug("Pipeline state changed from %s to %s." %
                              (old_state.value_nick, new_state.value_nick))
                        break

        return True
        #while self.pipeline.get_state(False)[1] == Gst.State.PLAYING:
        #    print("Playing")
        #    time.sleep(1)
        #pipestr = "filesrc location=%s ! capsfilter caps=video/mpegts,systemstream=\(boolean\)true,packetsize=\(int\)188 ! "\
                   #"tsdemux name=dem ! queue ! video/x-h264 ! filesink location=%s "%(infile, of_vid)#\
                   #"dem. ! queue ! audio/mpeg ! filesink location=%s"%(infile, of_vid, of_aud)
        #self.pipeline = Gst.parse_launch(pipestr)

    def make_wavescope(self, dirname, filename):
        pass

    def write_dirstate(self, dirname, status):
        # append a status file into the directory dirname

        if status in ["ERROR", "STARTED", "PAUSED", "STOPPED",
                      "SPLITTING", "SPLIT", "SPLITCOMPLETE", "SPLIT\tSTREAM1", "SPLIT\tSTREAM2",
                      "ANALYZING", "ANALYZED",
                      "MANIFESTING", "MANIFESTED",
                      "ZIPPING", "ZIPPED",
                      "UPLOADING", "UPLOADED"] or status.startswith("WFIID"):
            now = datetime.datetime.utcnow().replace(microsecond=0)
            try:
                with open(caproot + "/" + dirname + "/.RECSTATE", "a+") as f:
                    f.write(now.strftime("%Y-%m-%dT%H:%M:%SZ") + "\t" + status + "\n")
                    logging.debug("Wrote status %s into %s"%(status, dirname))
            except:
                logging.error("Could not write state %s in dir %s."%(status, dirname))
        else:
            logging.error("Cannot set weird status %s."%status)


    def read_statefile(self, dirname):
        # read the statusfile .RECSTATE from dirname
        logging.debug("Attempting to read record state from %s/.RECSTATE"%dirname)
        fn = caproot + "/" + dirname + "/.RECSTATE"
        try:
            with open(fn, 'r') as f:
                content = [x.strip('\n').split("\t") for x in f]
                return content
        except:
            logging.error("No status file found in %s"%dirname)
            return []

    def get_last_state(self, dirname, n=-1):
        # call the status file reader and return the last line

        try:
            return self.read_statefile(dirname)[n]
        except IndexError:
            return []

    def write_manifest(self, dirname, wfiid, duration, start, fns, flavors):
        logging.info("Writing manifest.xml for WFIID %s, FNs %s, flavors %s"%(wfiid, fns, flavors))
        with open('manifest_template.xml', 'r') as f:
            template = f.read()

        #todo check ob duration > 0, id != none, start ein ISOSTRING
        if not wfiid == None:
            template = template.replace("___ID___", "id=\"" + str(wfiid) +"\"")
        else:
            logging.error("No WFIID found! Manifest possibly wrong!")
            # todo er muss doch was machen wenn ich eine unscheduled recording habe!

        try:
            #b = datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%S")
            #todo sauber machen
            if len(start) > 3:
                template = template.replace("___START___", "start=\"" + str(start) + "\"")
        except:
            logging.error("START is no ISO string (%s) - not writing start time!"%start)
            template = template.replace("___START___", "")

        if float(duration) > 0:
            template = template.replace("___DURATION___", "duration=\"" + str(duration) + "\"")
        else:
            template = template.replace("___DURATION___", "")

        suffixdict = {
            "mkv": "video/x-matroska",
            "mp4": "video/mp4",
            "aac": "audio/aac",
            "mp2": "audio/mpeg",
            "mp3": "audio/mpeg3",
            "wav": "audio/wav"
        }
        tracks = ""
        tpls = zip(fns, flavors)
        for tpl in tpls:

            # try to guess correct mimetype
            suffix = tpl[0].split(".")[-1]

            try:
                mimetype = suffixdict[suffix]
            except KeyError:
                mimetype = "application/octet-stream"

            tracks += "\t<track type=\"%s\" id=\"%s\">\n"%(tpl[1], tpl[1].replace("/", "-"))
            tracks += "\t\t<mimetype>%s</mimetype>\n"%mimetype
            tracks += "\t\t<tags/>\n"
            tracks += "\t\t<url>%s</url>\n"%tpl[0]
            tracks += "\t</track>\n"

        template = template.replace("___TRACKS___", tracks)
        fn_mani = caproot + "/" + dirname + "/manifest.xml"
        with open(fn_mani, "w") as manifest_h:
            manifest_h.write(template)


        return True

    def get_seriesdata(self, seriesid):
        url = "%s/series/%s.xml" % (CONFIG['server']['url'], seriesid)
        try:
            xmlstring = self.curlreq(url).decode("UTF-8")
            print(xmlstring)
        except Exception:
            return ""

        root = ElementTree.fromstring(xmlstring)
        xmlstring = (self.xml_prettify(root))
        return xmlstring

    def get_episodedata(self, wfiid, add_dates=True):
        url = "%s/recordings/%s.xml" % (CONFIG['server']['url'], wfiid)
        try:
            xmlstring = self.curlreq(url).decode("UTF-8")
        except Exception:
            return False

        if add_dates == True:
            # there are two lines missing in the episode.xml returned from the /recordings endpoint which are
            # interpreted by both the ILIAS plugin and the OC overwiew. Add them before the end of the file.
            startdate = self.get_instancedata(wfiid)['start']
            addxml =    "<dcterms:recordDate>%s</dcterms:recordDate>" \
                        "<dcterms:created>%s</dcterms:created>"%(startdate, startdate)
            xmlstring = xmlstring.replace("</dublincore>", addxml + "</dublincore>")

        root = ElementTree.fromstring(xmlstring)
        xmlstring = (self.xml_prettify(root))
        return xmlstring

    def write_seriesxml(self, dirname, seriesxml):
        with open(caproot + "/" + dirname + "/series.xml", "w") as series_h:
            series_h.write(seriesxml)

    def write_episodexml(self, dirname, episodexml):
        with open(caproot + "/" + dirname + "/episode.xml", "w") as epi_h:
            epi_h.write(episodexml)

    def xml_prettify(self, elem):
        rough_string = ElementTree.tostring(elem, 'utf-8')
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ")

Gst.init(None)
GObject.threads_init()
ing = Ingester()
#print(ing.get_media_length("/srv/recordings/test1", "stream1.mpegts"))
#ing.zipdir("test1", "src1.mkv")
#ing.ingestscanloop()
#ing.splitfile("/srv/recordings/test1", "stream1.mpegts", "src1.mkv", "src1.aac")
#ing.write_manifest("/srv/recordings/test1", "aaaaa", "12343435", "2016-04-12T09:30:00Z", ("src1.aac", "src1.mkv", "src2.mkv"), ("presenter-audio/source", "presenter/source", "presentation/source"))
#ing.ingestscanloop()

def testfilecatcher(id, dir):
    testdir = dir
    testid = id
    fns = ("src1.mkv", "src1.aac", "src2.mkv")
    flavors = ("presentation/source", "presenter-audio/source", "presenter/source")
    a = ing.get_instancedata(testid)
    seriesxml = ing.get_seriesdata(a['seriesid'])
    episodexml = ing.get_episodedata(testid)
    ing.write_seriesxml(testdir, seriesxml)
    ing.write_manifest(testdir, testid, a['duration'], a['start'], fns, flavors)
    ing.write_episodexml(testdir, episodexml)

#testfilecatcher()

# Free resources.
#pipeline.set_state(gst.STATE_NULL)

# https://opencast.jira.com/wiki/display/MH/Capture+Agent+Communication+Protocols
# Ablauf:
# POST /capture-admin/agents/ address, state=idle
# POST /capture-admin/recordings/$RECORDING_ID state=manifest
# POST /capture-admin/recordings/$RECORDING_ID state=upload
# POST /capture-admin/recordings/$RECORDING_ID state=upload_finished
