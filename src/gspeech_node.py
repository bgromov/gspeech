#!/usr/bin/env python
# -*- coding: utf-8 -*-
#########################################################################################
#                                    _                                                  #
#      __ _ ___ _ __   ___  ___  ___| |__                                               #
#     / _` / __| '_ \ / _ \/ _ \/ __| '_ \                                              #
#    | (_| \__ \ |_) |  __/  __/ (__| | | |                                             #
#     \__, |___/ .__/ \___|\___|\___|_| |_|                                             #
#     |___/    |_|                                                                      #
#                                                                                       #
# ros package for speech recognition using Google Speech API                            #
# run using 'rosrun gspeech gspeech.py'                                                 #
# it creats and runs a node named gspeech                                               #
# the node gspeech publishes two topics- /speech and /confidence                        #
# the topic /speech contains the reconized speech string                                #
# the topic /confidence contains the confidence level in percentage of the recognization#
#                                                                                       #
#                                                                                       #
# UPDATE: for key generation look http://www.chromium.org/developers/how-tos/api-keys   #
#         at the revision date, each key allows your node to make up to 50 request      #
#         change in the cmd2 at the end of the string "yourkey" for your key            #
#                                                                                       #
# written by achuwilson                                                                 #
# revision by pexison                                                                   #
#                                                                                       #
# 30-06-2012 , 3.00pm                                                                   #
# achu@achuwilson.in                                                                    #
# 01-04-2015 , 11:00am                                                                  #
# pexison@gmail.com                                                                     #
#########################################################################################

import json, shlex, socket, subprocess, sys, threading
import roslib; roslib.load_manifest('gspeech')
import rospy
from gspeech.msg import SpeechStamped
import shlex,subprocess,os
from std_srvs.srv import *

class GSpeech(object):
  """Speech Recogniser using Google Speech API"""

  def __init__(self, _api_key, _lang):
    """Constructor"""
    # configure system commands
    self.api_key = _api_key
    self.lang = _lang
    self.actual_rate = 44100
    self.sox_cmd = "sox -r 44100 -t coreaudio default recording.flac silence 1 0.05 1% 1 0.3 1%"
    self.sox_args = shlex.split(self.sox_cmd)
    self.length_cmd = "soxi -D recording.flac" # returns length in seconds
    self.length_args = shlex.split(self.length_cmd)
    self.rate_cmd = "soxi -r recording.flac" # returns sampling rate in Hz
    self.rate_args = shlex.split(self.rate_cmd)
    self.wget_cmd = ("wget -q -U \"Mozilla/5.0\" ") + \
        ("--post-file recording.flac ") + \
        ("--header=\"Content-Type: audio/x-flac; rate={actual_rate}\" -O - ") + \
        ("\"https://www.google.com/speech-api/v2/recognize") + \
        ("?output=json&lang={lang}&key={api_key}\"")

    ## Moved to do_recognition()
    # self.wget_cmd = self.wget_cmd.format(actual_rate=self.actual_rate, api_key=self.api_key, lang=self.lang)
    # self.wget_args = shlex.split(self.wget_cmd)

    # start ROS node
    rospy.init_node('gspeech')
    # configure ROS settings
    rospy.on_shutdown(self.shutdown)
    self.pub_speech = rospy.Publisher('~speech', SpeechStamped, queue_size=10)
    self.srv_start = rospy.Service('~start', Empty, self.start)
    self.srv_stop = rospy.Service('~stop', Empty, self.stop)
    # run speech recognition
    self.started = True
    self.recog_thread = threading.Thread(target=self.do_recognition, args=())
    self.recog_thread.start()

  def start(self, req):
    """Start speech recognition"""
    if not self.started:
      self.started = True
      if not self.recog_thread.is_alive():
        self.recog_thread = threading.Thread(
          target=self.do_recognition, args=()
        )
        self.recog_thread.start()
      rospy.loginfo("gspeech recognizer started")
    else:
      rospy.loginfo("gspeech is already running")
    return EmptyResponse()

  def stop(self, req):
    """Stop speech recognition"""
    if self.started:
        self.started = False
        if self.recog_thread.is_alive():
            self.recog_thread.join()
        rospy.loginfo("gspeech recognizer stopped")
    else:
        rospy.loginfo("gspeech is already stopped")
    return EmptyResponse()

  def shutdown(self):
    """Stop all system process before killing node"""
    self.started = False
    if self.recog_thread.is_alive():
      self.recog_thread.join()
    self.srv_start.shutdown()
    self.srv_stop.shutdown()

  def do_recognition(self):
    """Do speech recognition"""
    while self.started:
      sox_p = subprocess.call(self.sox_args)
      end_time = rospy.Time.now()
      audio_len, _dummy_err = subprocess.Popen(self.length_args, stdout=subprocess.PIPE).communicate()
      start_time = end_time - rospy.Duration(float(audio_len.strip()))

      actual_rate, _dummy_err = subprocess.Popen(self.rate_args, stdout=subprocess.PIPE).communicate()
      self.actual_rate = int(actual_rate.strip())

      self.wget_cmd = self.wget_cmd.format(actual_rate=self.actual_rate, api_key=self.api_key, lang=self.lang)
      self.wget_args = shlex.split(self.wget_cmd)

      wget_out, wget_err = subprocess.Popen(
        self.wget_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
      ).communicate()

      # print wget_out
      # print wget_err

      if not wget_err and len(wget_out) > 16:
        wget_out = wget_out.split('\n', 1)[1]
        a = json.loads(wget_out)['result'][0]
        if 'transcript' in a['alternative'][0]:
          text = a['alternative'][0]['transcript']
          rospy.loginfo("text: {}".format(text))
        if 'confidence' in a['alternative'][0]:
          confidence = a['alternative'][0]['confidence']
          confidence = confidence * 100
          rospy.loginfo("confidence: {}".format(confidence))

        msg = SpeechStamped()
        msg.header.stamp = start_time
        msg.header.frame_id = "human_frame"
        msg.duration = end_time - start_time
        msg.text = text
        msg.confidence = confidence
        self.pub_speech.publish(msg)

def is_connected():
  """Check if connected to Internet"""
  try:
    # check if DNS can resolve hostname
    remote_host = socket.gethostbyname("www.google.com")
    # check if host is reachable
    s = socket.create_connection(address=(remote_host, 80), timeout=5)
    return True
  except:
    pass
  return False

def usage():
  """Print Usage"""
  print "Usage:"
  print "rosrun gspeech gspeech.py <API_KEY> [LANG=en-us]"


def main():
  if len(sys.argv) < 2:
    usage()
    sys.exit("No API_KEY provided")
  if not is_connected():
    sys.exit("No Internet connection available")
  api_key = str(sys.argv[1])
  if len(sys.argv) == 3:
    lang = str(sys.argv[2])
  else:
    lang = "en-us" # default
    print "Language is not specified. Using default:", lang
  speech = GSpeech(api_key, lang)
  rospy.spin()

if __name__ == '__main__':
  try:
    main()
  except rospy.ROSInterruptException:
    pass
  except KeyboardInterrupt:
    sys.exit(0)
