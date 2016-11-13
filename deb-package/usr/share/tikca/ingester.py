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
parser.add_argument("--tikcacfg", help="the tikca configfile", default='/etc/tikca.conf')
args = parser.parse_args()

#print(args.tikcacfg)
TIKCFG = ConfigObj(args.tikcacfg, list_values=True)
caproot = TIKCFG['capture']['directory']

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s %(levelname)-8s '
                               + '[%(filename)s:%(lineno)s:%(funcName)s()] %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')

    console = logging.FileHandler(TIKCFG['logging']['ingestfn'], mode='a', encoding="UTF-8")
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s %(levelname)-8s '
                                  + '[%(filename)s:%(lineno)s:%(funcName)s()] %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)

class Ingester:
    global TIKCFG
    global caproot
    def __init__(self):
        if TIKCFG['ingester']['autoingest'] == True \
                or __name__ == "__main__":
            logging.info("STARTING INGEST LOOP...")
            ingthr = threading.Thread(target=self.ingestscanloop)
            #ingthr.daemon = True
            ingthr.start()

    def __main__(self):
        print("bla")

    def get_media_length(self, dirname, fn):
        if TIKCFG['ingester']['probe'] == "ffprobe":
            comm = "%s -i %s -print_format json -show_streams" % (
            TIKCFG['ingester']['probe'], caproot + "/" + dirname + "/" + fn)
        elif TIKCFG['ingester']['probe'] == "avprobe":
            comm = "%s -show_format -of json -show_streams %s" % (
            TIKCFG['ingester']['probe'], caproot + "/" + dirname + "/" + fn)

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
                if os.path.isfile(caproot + "/" + dirname + "/" + TIKCFG['capture']['src1_fn_vid']):
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
                    if os.path.isfile(caproot + "/" + dirname + "/" + TIKCFG['capture']['src2_fn_vid']):
                        fns.append(TIKCFG['capture']['src2_fn_vid'])
                        if src2_max == "C":
                            src2_flavor = "presenter/source"
                        else:   # if max = P or max = B
                            src2_flavor = "presentation/source"
                except KeyError:
                    logging.debug("No filename for SRC2 set. Ignoring.")
                    src2_flavor = None


            # If we've got two video files, a new problem arises:
            # We cannot have 2x"presenter/source", neither 2x"presentation/source".
            try:
                src2_flavor
                if src1_flavor == "presenter/source" and src2_flavor == "presenter/source":
                    src2_flavor = "presentation/source"
                elif src1_flavor == "presentation/source" and src2_flavor == "presentation/source":
                    src2_flavor = "presentation2/source"
                logging.info("Flavor of source 1: '%s'" % src1_flavor)
                logging.info("Flavor of source 2: '%s'" % src2_flavor)
                if not src1_flavor == None: flavors.append(src1_flavor)
                if not src2_flavor == None: flavors.append(src2_flavor)

            except:
                logging.debug("Only having one video file in %s; not trying to assume which is presentation and which is presenter.")
                logging.info("Flavor of source 1: '%s'" % src1_flavor)
                if not src1_flavor == None: flavors.append(src1_flavor)

            # take one sound file: that which has less 'B' marks
            if src1_stats['C'] + src1_stats['P'] >= src2_stats['C'] + src2_stats['P']:
                if os.path.isfile(caproot + "/" + dirname + "/" + TIKCFG['capture']['src1_fn_aud']):
                    fns.append(TIKCFG['capture']['src1_fn_aud'])
            else:
                try:
                    TIKCFG['capture']['src2_fn_aud']
                    if os.path.isfile(caproot + "/" + dirname + "/" + TIKCFG['capture']['src2_fn_aud']):
                        fns.append(TIKCFG['capture']['src2_fn_aud'])
                except KeyError:
                    # send the SRC1 audio file even if it is "black", so we've got something to send
                    if os.path.isfile(caproot + "/" + dirname + "/" + TIKCFG['capture']['src1_fn_aud']):
                        fns.append(TIKCFG['capture']['src1_fn_aud'])
            flavors.append("presenter-audio/source")


        except FileNotFoundError:
            logging.error("No stream log file in directory '%s'! Using default flavor names."%dirname)
            # define standard flavours, if there is no analyze file. Take care of single stream configs.
            try:
                stdfnsflvs = {TIKCFG['capture']['src1_fn_aud']: TIKCFG['capture']['src1_stdflavor'],
                          TIKCFG['capture']['src1_fn_vid']: TIKCFG['capture']['stdflavor_audio'],
                          TIKCFG['capture']['src2_fn_aud']: "presenter/backup",
                          TIKCFG['capture']['src2_fn_vid']: TIKCFG['capture']['src1_stdflavor']}
            except KeyError:
                stdfnsflvs = {TIKCFG['capture']['src1_fn_aud']: TIKCFG['capture']['src1_stdflavor'],
                              TIKCFG['capture']['src1_fn_vid']: TIKCFG['capture']['stdflavor_audio']}

            for stdfn, stdflv in stdfnsflvs.items():
                fn = caproot + "/" + dirname + "/" + stdfn
                logging.debug("Looking for file %s..."%fn)
                if os.path.isfile(fn):
                    fns.append(stdfn)
                    flavors.append(stdflv)

        logging.debug("File list: %s, %s"%(flavors, fns))
        if len(fns) < 1:
            logging.error("Did not find any files in analyzing dir '%s'. This will not be uploaded."%dirname)
            self.write_dirstate(dirname, "ERROR")
        with open(caproot + "/" + dirname + "/.ANA", "w") as anafile:
            anafile.write(json.dumps((flavors, fns)))

        return (flavors, fns)

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
                    (TIKCFG['server']['username'], TIKCFG['server']['password']))
            curl.setopt(curl.HTTPHEADER, ['X-Requested-Auth: Digest'])

        # activate progress function
        if showprogress:
            self.progress_called = 0
            logging.debug("Showing progress of upload...")

            try:
                curl.setopt(curl.NOPROGRESS, False)
                curl.setopt(curl.XFERINFOFUNCTION, self.progress)
            except:
                pass
        xferstart = datetime.datetime.now()
        #try:
        # todo fetch all kinds of errors in a good way
        try:
            curl.perform()
        except:
            logging.error("CURL error. Could not connect to '%s'."%url)

        if showprogress:
            logging.info('Transfer duration: {}'.format(datetime.datetime.now() - xferstart))

        status = curl.getinfo(pycurl.HTTP_CODE)
        curl.close()
        if int(status / 100) != 2:
            logging.error("ERROR: Request to '%s' failed (HTTP status code %i)"%(url, status))
            result = buf.getvalue()
            logging.error("Result: %s"%result)
        result = buf.getvalue()

        buf.close()
        return result


    def progress(self, download_t, download_d, upload_t, upload_d):
        self.progress_called += 1
        if upload_t > 0 and self.progress_called % 10000 == 0:
            logging.debug("Uploaded {0} % of {1} MB.".format(round(upload_d/upload_t*100, 1), round(upload_t/1024/1024)))

    def get_instancedata(self, wfiid):
        # get instance data from wfiid
        url = "%s/workflow/instance/%s.json"%(TIKCFG['server']['url'], wfiid)
        #logging.debug(url)
        try:
            jsonstring = self.curlreq(url).decode("UTF-8")
            jsondata = json.loads(jsonstring)
            mediapackage = jsondata['workflow']['mediapackage']
        except Exception:
            return dict()


        retdict = {'title': mediapackage['title']}
        try:
            retdict['duration'] = round(float(mediapackage['duration']))
        except KeyError:
            retdict['duration'] = None

        retdict['start'] = mediapackage['start']

        try:
            retdict['seriesid'] = mediapackage['series']
            retdict['seriestitle'] = mediapackage['seriestitle']
        except KeyError:
            retdict['seriesid'] = retdict['seriestitle'] = None

        return retdict

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
        try:
            pot_wfiid = states[0][1].split("\t")[1]
            if not pot_wfiid == "None":
                logging.debug("Found WFIID %s in dir %s"%(pot_wfiid, subdir))
                return pot_wfiid
        except:
            logging.debug("Did not find WFIID in dir %s. First line reads '%s'" % (subdir, states[0]))
            return None

    def get_start_from_episodexml(self, subdir):
        try:
            with open(caproot + '/' + subdir + '/episode.xml', 'r') as f:
                episode = f.read()
            theline = episode.split("start=")[1]
            ts = theline.split(";")[0]
            if len(ts) > 0:
                return date
            else:
                logging.info("Did not get a valid date from '%s/episode.xml'. Returning button-press-date instead."%subdir)
                return self.get_start_from_dir(subdir)
        except:
            logging.info("Did not get a valid date from '%s/episode.xml'. Returning button-press-date instead."%subdir)
            return self.get_start_from_dir(subdir)

    def get_start_from_dir(self, subdir):
        # get tuples from statefile like "['2016-11-04T12:01:33Z', 'STOPPED']"
        states = self.read_statefile(subdir)
        for stateline in states:
            if len(stateline) > 0:
                if stateline[1] == "RECORDING":
                    return stateline[0]

        logging.debug("Found no valid start time for recording in %s."%subdir)
        return "1970-01-01T00:00:00Z"

    def get_stop_from_dir(self, subdir):
        states = self.read_statefile(subdir)
        for stateline in states:
            if len(stateline) > 0:
                if stateline[1] == "STOPPED":
                    return stateline[0]

        return "1970-01-01T00:00:00Z"


    def ingestscanloop(self, scanroot = caproot):
        # list dirs
        while True:
            dirlist = []

            for entry in os.listdir(scanroot):
                if not entry.startswith('.') \
                        and not entry == 'lost+found' \
                        and not entry == TIKCFG['ingester']['moveto'] \
                        and os.path.isdir(scanroot + "/" + entry):
                    dirlist.append(entry)
            print(dirlist)
            self.queue = []

            for dirtoscan in dirlist:
                # open status of recording in directory
                logging.info("Scanning dir '%s'..."%dirtoscan)
                ls = self.get_last_state(dirtoscan)
                if len(ls) > 1:
                    logging.debug("Last state in '%s': '%s'"%(dirtoscan, ls))
                else:
                    logging.info("No directory information here. Maybe this is no directory for us - leaving.")

                try:
                    self.queue.append((dirtoscan, ls[0], ls[1]))
                except IndexError:
                    logging.error("Found dir '%s' with no status information. Ignoring." % dirtoscan)


            for task in self.queue:
                wfiid = self.get_wfiid_from_dir(task[0])
                if "ING" in task[2]:
                    logging.debug("Dir '%s' seems to have work in progress ('%s' since %s). Not starting anything new."%(task[0], task[2], task[1]))

                    #todo gucken, dass gefailte/gestoppte ingests (zeit liegt zu lange zurück) neu gestartet werden
                    # timedelta(task[1], now()) > 60 min

                elif task[2] == "STOPPED":
                    logging.info("Directory %s contains recorded files. Starting split step."%task[0])
                    # todo: in einen extrathread packen
                    self.write_dirstate(task[0], "SPLITTING")
                    if self.splitfile_new(task[0], TIKCFG['capture']['src1_fn_orig'], TIKCFG['capture']['src1_fn_vid'],
                                       TIKCFG['capture']['src1_fn_aud']):
                        self.write_dirstate(task[0], "SPLIT;STREAM1")
                        errstate = False
                    else:
                        self.write_dirstate(task[0], "ERROR")
                        errstate = True

                    # only attempt to split the second file if there is a name defined
                    try:
                        TIKCFG['capture']['src2_fn_orig']
                        if self.splitfile_new(task[0], TIKCFG['capture']['src2_fn_orig'],
                                        TIKCFG['capture']['src2_fn_vid'], TIKCFG['capture']['src2_fn_aud']):
                            self.write_dirstate(task[0], "SPLIT;STREAM2")
                            errstate = False
                        else:
                            self.write_dirstate(task[0], "ERROR")
                            errstate = True
                    except:
                        logging.info("This is a single stream recorder. Not trying to split second stream.")

                    if errstate == False:
                        self.write_dirstate(task[0], "SPLITCOMPLETE")
                        self.ingestscanloop()






                elif task[2] == "SPLITCOMPLETE":
                    logging.info("Directory %s contains split files. Starting analyze step."%task[0])
                    self.write_dirstate(task[0], "ANALYZING")
                    flavors, fns = self.analyze_stats(task[0])

                    # get the file length (in seconds)
                    flength = self.get_media_length(task[0], TIKCFG['capture']['src1_fn_orig'])
                    # sometimes we don't get a file length from the original file.
                    # The audio file, though, contains a valid length most of the time. So we might as well try.
                    if flength < 1:
                        flength = self.get_media_length(task[0], TIKCFG['capture']['src1_fn_aud'])

                    # Note: We are not getting the real start time ("when did somebody push the play button?"), but
                    # the start time from the episode.xml. If you wanna change this behaviour, this is the line:
                    # start = self.get_start_from_dir(task[0])
                    start = self.get_start_from_episodexml(task[0])

                    # there have to be filenames to write a manifest about, otherwise we're throwing an error
                    # todo: potential error if we do not have a wfiid, right...?
                    if len(fns) > 0:
                        self.set_oc_recstate("manifest", wfiid)
                        ret = self.write_manifest(
                            dirname=task[0], wfiid=wfiid, duration=flength, start=start, fns=fns, flavors=flavors
                        )
                        if ret:
                            self.write_dirstate(task[0], "ANALYZED")
                            self.ingestscanloop()
                        else:
                            self.write_dirstate(task[0], "ERROR")
                    else:
                        logging.error("Did not find any files in dir '%s'"%task[0])
                        self.write_dirstate(task[0], "ERROR")




                elif task[2] == "ANALYZED":
                    logging.info("Directory %s is analyzed. Starting upload step."%task[0])
                    self.write_dirstate(task[0], "UPLOADING")
                    if not wfiid == None:
                        instancedict = self.get_instancedata(wfiid)
                        try:
                            seriesxml = self.get_seriesdata(instancedict['seriesid'])

                        except KeyError:
                            seriesxml = ""

                        self.write_seriesxml(task[0], seriesxml)

                        try:
                            episodexml = self.get_episodedata(wfiid)
                            self.write_episodexml(task[0], episodexml)
                        except KeyError:
                            logging.error("No episode data. what shall i do? klappt das hier, wenn es keine episode gibt?")


                    else:
                        # this seems to be an unscheduled recording. We will be treating it as such:
                        try:
                            self.write_seriesxml(task[0], self.get_seriesdata(TIKCFG['unscheduled']['serid']))
                        except:
                            logging.error("Cannot get series data for unscheduled recordings. Not doing anything for this directory.")
                            return False

                        # read episode template
                        with open('episode_template.xml', 'r') as f:
                            epitemp = f.read()

                        startdate = self.get_start_from_dir(task[0])
                        stopdate = self.get_stop_from_dir(task[0])
                        caname = TIKCFG['agent']['name']

                        # replace values with something we can identify
                        epitemp = epitemp.replace("___CREATOR___", "Unknown Creator")
                        epitemp = epitemp.replace("___SERID___", TIKCFG['unscheduled']['serid'])
                        epitemp = epitemp.replace("___START___", startdate)
                        epitemp = epitemp.replace("___END___", stopdate)
                        epitemp = epitemp.replace("___TITLE___", "Unscheduled recording from CA %s"%caname)
                        epitemp = epitemp.replace("___CANAME___", caname)

                        self.write_episodexml(task[0], epitemp)
                        logging.info("Writing episode data for unscheduled recording...")

                    # read results of analyze-step to get file names etc.
                    with open(caproot + "/" + task[0] + "/.ANA", "r") as anafile:
                        jsoncontent = anafile.read()
                        flavors, fns = json.loads(jsoncontent)

                    #try:
                    self.ingest(fns, flavors, subdir=task[0], wfiid=wfiid)
                    self.write_dirstate(task[0], "UPLOADED")
                    #except:
                    #    logging.error("Upload of wfiid '%s', directory '%s', did not work."%(wfiid, task[0]))
                    #    self.write_dirstate(task[0], "ERROR")

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

                elif task[2] == "UPLOADED":
                    if len(TIKCFG['ingester']['moveto']) > 0:
                        logging.info("Moving dir '%s' to subdir '%s'."%(task[0], TIKCFG['ingester']['moveto']))
                        self.move_ingested(task[0])
                    else:
                        logging.debug("Directory %s has already been ingested, skipping..."%task[0])

                elif task[2] == "ERROR":
                    step_before_error = self.get_last_state(dirtoscan, -3)
                    logging.info("Found dir %s in an error state after '%s'."%(task[0], step_before_error))




            logging.info("Waiting for %s min(s) to scan again."%TIKCFG['ingester']['looptime'])
            time.sleep(float(TIKCFG['ingester']['looptime']) * 60)

    def get_wfcfg(self, wfiid):
        # this is currently not being used.

        param = []
        url = "%s/instance/%s.json"%(TIKCFG['server']['url'], wfiid)
        confjson = json.loads(self.curlreq(url))

        for prop in confjson['workflow']['configurations']:
            param.append((prop['key'], prop['$']))
        return param

    def move_ingested(self, dirname):
        if not os.path.exists(caproot + "/" + TIKCFG['ingester']['moveto']):
            try:
                os.makedirs(caproot + "/" + TIKCFG['ingester']['moveto'])
            except:
                logging.error("Could not create directory for already ingested files.")
                return False
        if os.rename(caproot + "/" + dirname,
                     caproot + "/" + TIKCFG['ingester']['moveto'] + "/" + dirname):
            logging.info("Moved dir '%s' to directory for already ingested files."%dirname)
            return True

    def get_agentprops(self, wfiid):

        # this code snippet is partly stolen from Lars Kiesow's pyCA

        param = []
        wdef = TIKCFG['unscheduled']['workflow']

        if len(wfiid) > 1:
            url = "%s/recordings/%s/agent.properties"%(TIKCFG['server']['url'], wfiid)
            properties = self.curlreq(url).decode('utf-8')

            for prop in properties.split('\n'):
                if prop.startswith('org.opencastproject.workflow.config'):
                    key, val = prop.split('=', 1)
                    key = key.split('.')[-1]
                    param.append((key, val))
                elif prop.startswith('org.opencastproject.workflow.definition'):
                    wdef = prop.split('=', 1)[-1]
            return wdef, param

    def set_oc_castate(self, state):
        # Register CA in OC and send status updates
        if state in ("idle", "error", "capturing", "idle"):
            postdata = [('address', TIKCFG['agent']['address']), ('state', state)]
            endpoint = "%s/capture-admin/agents/%s"%(TIKCFG['server']['url'], TIKCFG['agent']['name'])
        response = self.curlreq(endpoint, postdata)
        """try:

            logging.info(response)
        except:
            logging.info(response)
            logging.warning("Could not send capture agent state '%s' to '%s'"%(state, endpoint))
            return False"""
        return True

    def set_oc_recstate(self, state, recid):
        # Set status of recording (not the same as status of CA, see set_oc_castate)
        if state in ("capturing", "manifest", "upload", "upload_finished") and not recid == None:
            postdata = [('state', state)]
            url = "%s/capture-admin/recordings/%s"%(TIKCFG['server']['url'], recid)
            try:
                response = self.curlreq(url, postdata)
                logging.debug("Response from %s: '%s'"%(url, response))
                return True
            except:
                logging.warning("Could not send recording state for recording ID '%s' to '%s'"%(recid,endpoint))
                return False
        else:
            return False


    def ingest(self, fns, flavors, subdir, wfiid=None, wfdef=TIKCFG['unscheduled']['workflow']):
    # this code snippet is mostly stolen from Lars Kiesow's pyCA.

        if not wfiid == None:
            self.set_oc_recstate("upload", wfiid)

        logging.info('Creating new mediapackage')
        mediapackage = self.curlreq('%s/ingest/createMediaPackage'%TIKCFG['server']['url'])
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
            mediapackage = self.curlreq('%s/ingest/addDCCatalog'%TIKCFG['server']['url'], list(fields))

        # add series DublinCore catalog
        if os.path.isfile('%s/series.xml' % recording_dir):
            logging.info('Uploading series DC catalog')
            dublincore = ''
            with open('%s/series.xml' % recording_dir, 'r') as seriesfile:
                dublincore = seriesfile.read().encode('utf8', 'ignore')
            fields = [('mediaPackage', mediapackage),
                      ('flavor', 'dublincore/series'),
                      ('dublinCore', dublincore)]
            mediapackage = self.curlreq('%s/ingest/addDCCatalog'%TIKCFG['server']['url'], list(fields))

        # add track(s)
        tpls = zip(fns, flavors)
        for tpl in tpls:
            logging.info("Uploading file %s (flavor %s)"%(tpl[0], tpl[1]))
            fullfn = caproot + "/" + subdir + "/" + tpl[0]
            # re-check whether filename exists!
            if os.path.isfile(fullfn):
                track = fullfn.encode('ascii', 'ignore')
                fields = [('mediaPackage', mediapackage), ('flavor', tpl[1]),
                          ('BODY1', (pycurl.FORM_FILE, track))]
                mediapackage = self.curlreq('%s/ingest/addTrack'%TIKCFG['server']['url'], list(fields), showprogress=True)
            else:
                logging.error("The file '%s' does not exist. I am not uploading it."%fullfn)



        # ingest if WFIID is set (i. e. scheduled recordings):
        if not wfiid == None:
            wfdef, wfcfg = self.get_agentprops(wfiid)

            logging.info('Finishing ingest by writing mediapackage and workflow config')
            fields = [('mediaPackage', mediapackage),
                      ('workflowDefinitionId', wfdef),
                      ('workflowInstanceId', wfiid.encode('ascii', 'ignore'))]
            fields += wfcfg

            mediapackage = self.curlreq('%s/ingest/ingest'%TIKCFG['server']['url'], fields)
            logging.info("Writing mediapackage info into directory...")
            with open(recording_dir + "/mediapackage.xml", "w") as f:
                f.write(mediapackage.decode('utf-8'))
            logging.info("Finished ingest of WFIID %s (dir '%s')"%(wfiid, subdir))
            # Finally, communicate with OC server and tell it that the uploading process is finished.
            self.set_oc_recstate("upload_finished", wfiid)

        # ingest if WFIID is not set (unscheduled recordings):
        else:
            wfdef = TIKCFG['unscheduled']['workflow']
            fields = [('mediaPackage', mediapackage),
                      ('workflowDefinitionId', wfdef)]

            mediapackage = self.curlreq('%s/ingest/ingest' % TIKCFG['server']['url'], fields)
            logging.info("Writing mediapackage info into directory...")
            with open(recording_dir + "/mediapackage.xml", "w") as f:
                f.write(mediapackage.decode('utf-8'))
            logging.info("Finished ingest of unscheduled recording from '%s')" % (subdir))



        return True


    def df(self, mode='mb'):
        statvfs = os.statvfs(TIKCFG['capture']['directory'])
        MB_free = round(statvfs.f_frsize * statvfs.f_bavail/1024/1024)
        #logging.debug("MB available: %i"%MB_free)
        return MB_free

    def splitwatchdog(self, dirname, of_vid, of_aud):
        time.sleep(5)
        if self.pipeline.get_state(False)[1] == Gst.State.PLAYING:
            logging.info("Splitting process in dir '%s':" % (dirname))
            logging.info("%s \t %s KB" % (of_vid, os.path.getsize(caproot + "/" + dirname + "/" + of_vid)))
            logging.info("%s \t %s KB" % (of_aud, os.path.getsize(caproot + "/" + dirname + "/" + of_aud)))
            self.splitwatchdog(dirname, of_vid, of_aud)
        else:
            logging.error("This pipeline doesn\'t run.")

    def splitfile_new(self, dirname, infile, of_vid, of_aud):
        # split mpegts streams into parts

        if not os.path.isfile(caproot + "/" + dirname + "/" + infile):
            logging.error("Error while splitting file: File does not exist ('%s')"%(caproot + "/" + dirname + "/" + infile))
            return False

        fsize = os.path.getsize(caproot + "/" + dirname + "/" + infile)
        if fsize < 640*1024: # 640 KB ought to be enough for everybody.
            logging.error("Infile '%s' is only %s byte(s) long. I am not splitting this."%(caproot + "/" + dirname + "/" + infile, fsize))
            self.write_dirstate(dirname, "ERROR")
            return False

        if self.df() < 1.5 * fsize / 1024 / 1024:
            logging.error("Not enough disk space! (%s MB free, %s MB needed)" % (self.df(), fsize / 1024 / 1024))
            self.write_dirstate(dirname, "ERROR")
            return False

        launchstr = "filesrc location=%s ! video/mpegts, systemstream=(boolean)true, packetsize=(int)188 ! " \
                    "tsdemux name=d ! queue2 ! video/x-h264 ! queue2 ! h264parse ! queue2 ! matroskamux ! queue2 ! filesink location=%s " \
                    "d. ! queue2 ! audio/mpeg, mpegversion=(int)2, stream-format=(string)adts ! queue2 ! filesink location=%s"%(
                     caproot + "/" + dirname + "/" + infile,
                     caproot + "/" + dirname + "/" + of_vid,
                     caproot + "/" + dirname + "/" + of_aud)

        wd = threading.Thread(target=self.splitwatchdog, kwargs={"dirname": dirname, "of_vid": of_vid, "of_aud": of_aud})
        wd.daemon = True
        wd.start()


        self.pipeline = Gst.parse_launch(launchstr)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        logging.debug("Setting GST pipeline to 'playing' - start of splitting process.")
        self.pipeline.set_state(Gst.State.PLAYING)

        # todo: Hier was hintun, das nach einer Minute abbricht, falls die Out-Dateien 0 Byte groß sind.

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
                    del wd
                    del self.pipeline
                    del bus
                    return True

                elif message.type == Gst.MessageType.STATE_CHANGED:
                    if isinstance(message.src, Gst.Pipeline):
                        old_state, new_state, pending_state = message.parse_state_changed()
                        logging.debug("Pipeline state changed from %s to %s." %
                                      (old_state.value_nick, new_state.value_nick))
                        break

        return True

    def write_dirstate(self, dirname, status):
        # append a status file into the directory dirname

        if status in ["IDLE", "ERROR", "STARTING", "STARTED", "RECORDING", "PAUSED", "STOPPING", "STOPPED",
                      "SPLITTING", "SPLIT", "SPLITCOMPLETE", "SPLIT;STREAM1", "SPLIT;STREAM2",
                      "ANALYZING", "ANALYZED",
                      "MANIFESTING", "MANIFESTED",
                      "ZIPPING", "ZIPPED",
                      "UPLOADING", "UPLOADED"] or status.startswith("WFIID"):
            now = datetime.datetime.utcnow().replace(microsecond=0)
            try:
                if dirname.startswith(caproot):
                    logname = dirname + "/.RECSTATE"
                else:
                    logname = caproot + "/" + dirname + "/.RECSTATE"
                with open(logname, "a") as f:
                    f.write(now.strftime("%Y-%m-%dT%H:%M:%SZ") + ";" + status + "\n")
                    logging.debug("Wrote status '%s' into dir '%s'"%(status, dirname))
            except:
                logging.error("Could not write state %s in dir %s."%(status, dirname))
        else:
            logging.error("Cannot set weird status %s."%status)


    def read_statefile(self, dirname):
        # read the statusfile .RECSTATE from dirname
        logging.debug("Attempting to read record state from '%s/.RECSTATE'"%dirname)
        fn = caproot + "/" + dirname + "/.RECSTATE"
        try:
            with open(fn, 'r') as f:
                content = [x.strip('\n').split(";") for x in f]
                return content
        except FileNotFoundError:
            logging.error("No status file found in '%s'."%dirname)
            return []

    def get_last_state(self, dirname, n=-1):
        # call the status file reader and return the last line

        try:
            return self.read_statefile(dirname)[n]
        except IndexError:
            return []

    def write_manifest(self, dirname, duration, start, fns, flavors, wfiid=None):
        logging.info("Writing manifest.xml for WFIID %s, FNs %s, flavors %s"%(wfiid, fns, flavors))
        with open('manifest_template.xml', 'r') as f:
            template = f.read()

        #todo check ob duration > 0, id != none, start ein ISOSTRING
        if not wfiid == None:
            template = template.replace("___ID___", "id=\"" + str(wfiid) +"\"")
        else:
            template = template.replace("___ID___", "")
            logging.info("No WFIID found! This seems to be an unscheduled recording.")

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
        url = "%s/series/%s.xml" % (TIKCFG['server']['url'], seriesid)
        try:
            xmlstring = curlreq(url).decode("UTF-8")
        except Exception:
            return ""

        root = ElementTree.fromstring(xmlstring)
        xmlstring = (self.xml_prettify(root))
        return xmlstring

    def get_episodedata(self, wfiid, add_dates=True):
        url = "%s/recordings/%s.xml" % (TIKCFG['server']['url'], wfiid)
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


# if this script is being called directly, start the ingest loop
if __name__ == "__main__":
    ing = Ingester()


#testfilecatcher()

# Free resources.
#pipeline.set_state(gst.STATE_NULL)

# https://opencast.jira.com/wiki/display/MH/Capture+Agent+Communication+Protocols
# Ablauf:
# POST /capture-admin/agents/ address, state=idle
# POST /capture-admin/recordings/$RECORDING_ID state=manifest


