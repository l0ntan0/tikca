#!/usr/bin/python3
import subprocess
import shlex
import glob
import os
import argparse
import logging

# add argument parser
parser = argparse.ArgumentParser()
parser.add_argument("dir", help="directory to check", type=str)
args = parser.parse_args()

# install:
# sudo apt-get install sox libsox-fmt-mp3

pathto = dict()
pathto['ffprobe'] = "/usr/local/bin/ffprobe"
pathto['ffmpeg'] = "/usr/local/bin/ffmpeg"
pathto['sox'] = "/usr/bin/sox"
pathto['tempdir'] = "/tmp/"


thresh = dict()
thresh['snd_pklevdb'] = -10  # Pk lev dB
thresh['snd_rmslevdb'] = -40   # RMS Pk dB
thresh['vid_detect'] = 0.1
thresh['vid_minchanges'] = 10
thresh['vid_anadur'] = 60*30
thresh['vid_anastart'] = 300


def video_tc(fn, short=False):

    fn_splitpath = os.path.splitext(fn)
    fn_out = fn_splitpath[0] + "_analyze" + ".mp4"

    fake = False
    if not fake and not short:
        print("Transcoding '%s' to '%s'..."%(fn, fn_out))
        a = subprocess.check_output([pathto['ffmpeg'], "-i", fn, "-y", "-c:v", "copy", "-an", "-loglevel", "-8", fn_out],
                                     universal_newlines=True, stderr=subprocess.STDOUT)


    if short and not fake:
        print("Transcoding '%s' to '%s' (starting point %s; duration %s s)..."%
              (fn, fn_out, str(thresh['vid_anastart']), str(thresh['vid_anadur'])))
        cmd = "%s -ss %s -i %s -t %s -y -c:v copy -an -loglevel -8 %s"\
              %(pathto['ffmpeg'], str(thresh['vid_anastart']), fn, str(thresh['vid_anadur']), fn_out)
        cmdlist = shlex.split(cmd)
        try:
            subprocess.check_output(cmdlist, universal_newlines=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError:
            print("Could not transcode %s to %s"%(fn, fn_out))
            return False

    return(fn_out)

def video_shotdetection(fn, threshold=thresh['vid_detect']):
    if not fsizeok(fn):
        print("File does not exist or is of 0 bytes length.")
        return [False, False]

    print("Shot detection for file '%s'..."%(fn))
    cmd = '%s -show_frames -of compact=p=0 -f lavfi "movie=%s,select=gt(scene\,%s)"'%\
          (pathto['ffprobe'], fn, str(threshold))
    print(cmd)
    cmdlist = shlex.split(cmd)

    a = subprocess.check_output(cmdlist, universal_newlines=True, stderr=subprocess.STDOUT)


    numscenes = a.count("lavfi.scene_score")
    print("Detected %i scene changes in file '%s'."%(numscenes, fn))
    if numscenes >= thresh['vid_minchanges']:
        enough = True
    else:
        enough = False
    return [enough, numscenes]

def check_audio(fn):

    print("Checking audio file %s..."%fn)
    if not fsizeok(fn):
        print("File does not exist or is of 0 bytes length.")
        return False


    try:
        a = subprocess.check_output([pathto['sox'], fn, "-n", "stats"],
                                    universal_newlines=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        print("SoX can't read file %s!"%fn)
        return [False, False]

    # init values
    pklevdb = -100.0
    rmslevdb = -100.0
    levelcheck = {"peak": False,
                 "rms": False}

    for line in a.split("\n"):
        if "Pk lev dB" in line:
            pklevdb = float(line.split("    ")[1].strip())
        if "RMS lev dB" in line:
            rmslevdb = float(line.split("    ")[1].strip())

    if pklevdb <= thresh['snd_pklevdb']:
        print("zu leiser peak: %2.3f"%pklevdb)
    else:
        print("peak level okay: %2.3f"%pklevdb)
        levelcheck['peak'] = True

    if rmslevdb <= thresh['snd_rmslevdb']:
        print("zu leiser rms: %2.3f"%rmslevdb)
    else:
        print("rms level okay: %2.3f"%rmslevdb)
        levelcheck['rms'] = True

    if levelcheck['rms'] and levelcheck['peak']:
        return [True, levelcheck['rms']]
    else:
        return [False, thresh['snd_rmslevdb']]


def optimize_audio(fn):
    print("Optimizing audio file %s..."%fn)
    if not fsizeok(fn):
        print("File does not exist or is of 0 bytes length.")
        return False

    fn_splitpath = os.path.splitext(fn)
    fn_backup = fn_splitpath[0] + "_backup" + fn_splitpath[1]
    fn_out = os.path.split(fn)[0] + "/out" + fn_splitpath[1]

    # if it's an mp3, we don't want to change the bitrate; if it's not, we do not have to add anything
    broption = ""
    if fn_splitpath[1] == ".MP3" or fn_splitpath[1] == ".mp3":
        # checking for bitrate. way too complicated - let's settle with 192k
        #print("checking bit rate...")
        #a = subprocess.check_output([pathto['sox'], "--i", fn],
        #                            universal_newlines=True, stderr=subprocess.STDOUT)
        #for line in a.split("\n"):
        #    if "Bit Rate" in line:
        #        br = line.split(": ")[1][0:-1]
        #        broption = "-C %s"%br
        #        print("Using bitrate option '%s'"%broption)
        broption = "-C 192"
        print("Using bitrate option '%s'"%broption)

    try:
        a = subprocess.check_output([pathto['sox'], "--norm", fn, broption, fn_out],
                                universal_newlines=True, stderr=subprocess.STDOUT)
        print(a)
    except subprocess.CalledProcessError:
        print("SoX can't read file %s!"%fn)
        return False

    os.rename(fn, fn_backup)
    os.rename(fn_out, fn)
    print("Optimized audio file %s!"%fn)
    return True

def fsizeok(fn):
    # check whether file exists and is > 0 bytes large
    try:
        sz = os.path.getsize(fn)
    except FileNotFoundError:
        return(False)

    if int(sz) > 0:
        return True
    else:
        return False

def dircheck(dn, streamtype, filetype):
    if not os.path.isdir:
        print("%s is not a directory. Aborting.")
        return False
    l_files = glob.glob(dn + "/*.%s"%filetype)

    filelist = []
    for fn in l_files:
        sz = os.path.getsize(fn)
        if streamtype == "audio":
            res = check_audio(fn)
            filelist.append((fn, sz, res[0], res[1]))

        if streamtype == "video":
            v_analyze_fn = video_tc(fn, True)
            res = video_shotdetection(v_analyze_fn)
            filelist.append((fn, sz, res[0], res[1]))

    return filelist

def do_dir(dn):
    types_to_check = [
        ("video", "mts"),
        ("video", "mkv"),
        ("video", "avi"),
        ("audio", "mp3"),
        ("audio", "mp2"),
        ("audio", "flac")
    ]

    summary = []
    for tp in types_to_check:
        res = dircheck(dn, tp[0], tp[1])
        print(res)
        summary.append(res)

    with open(dn + "summary.txt", "w") as f:
        f.write(str(summary))


do_dir(args.dir)
#check_audio("/home/pascal/testrec/USBAudio.mp3")
#optimize_audio("/home/pascal/testrec/USBAudio.mp3")

