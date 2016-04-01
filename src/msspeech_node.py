#!/usr/bin/env python
# -*- coding: utf-8 -*-
#########################################################################################
# ROS node for speech recognition using Microsoft Bing Voice Recognition API            #
# (Project Oxford - https://www.projectoxford.ai/ )                                     #
#                                                                                       #
# Based on gspeech node. See gspeech.py                                                 #
#                                                                                       #
# Author: Boris Gromov                                                                  #
# Date: 25 Mar 2016                                                                     #
#########################################################################################

import json, shlex, socket, subprocess, sys, threading, signal, tempfile
import uuid, platform, requests
import roslib; roslib.load_manifest('gspeech')
import rospy
from gspeech.msg import SpeechStamped
import shlex, subprocess, os
from std_srvs.srv import *

class Authorization(object):
  """OAuth client for Microsoft server"""

  def __init__(self, _client_id, _api_key):
    self.url = "https://oxford-speech.cloudapp.net/token/issueToken"
    self.payload = "grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}&scope=https%3A%2F%2Fspeech.platform.bing.com"
    self.headers = {
      'content-type': "application/x-www-form-urlencoded"
    }

    self.payload = self.payload.format(client_id = _client_id, client_secret = _api_key)

    rospy.loginfo("Authenticating...")
    response = requests.request("POST", self.url, data = self.payload, headers = self.headers)

    seconds = 10

    if response.status_code != 200:
      rospy.logerr("Failed to get authorization token")
    else:
      self.token = response.json()['access_token']
      self.expires_in = response.json()['expires_in']

      seconds = float(self.expires_in)

      rospy.loginfo("Received authorization token")

    # print(self.token)
    # seconds = 10

    # Make timer expire at 90% of actual time
    rospy.Timer(rospy.Duration(seconds - seconds / 10.0), self.callbackTokenExpired)

  def callbackTokenExpired(self, event):
    response = requests.request("POST", self.url, data = self.payload, headers = self.headers)
    self.token = response.json()['access_token']
    rospy.loginfo("Received new authorization token")
    # print(self.token)

class MSSpeech(object):
  """Speech Recogniser using Microsoft Bing Voice Recognition API (Project Oxford)"""

  def __init__(self, _api_key, _lang):
    """Constructor"""

    # generate random UUID for each request (moved to do_recognition())
    # self.request_id = uuid.uuid4()

    # generate persistent UUID based on the host's name
    self.instance_id = uuid.uuid5(uuid.NAMESPACE_URL, platform.node())
    self.app_id = "D4D52672-91D7-4C74-8AD8-42B1D98141A5" # Microsoft pre-defined
    # Speech API subscription key
    self.api_key = _api_key
    # locale
    self.lang = _lang
    self.actual_rate = 16000

    # configure system commands
    self.sox_cmd = "sox -r 16000 -e signed -b 16 -d -p silence 1 0.05 1% 1 0.3 1%"
    self.sox_args = shlex.split(self.sox_cmd)

    # this command will be piped from previous one
    self.soxconv_cmd = "sox -p -r 16000 -b 16 -c 1 {filename}"
    self.length_cmd = "soxi -D {filename}" # returns length in seconds
    self.rate_cmd = "soxi -r {filename}" # returns sampling rate in Hz

    # Speech API service
    self.url = "https://speech.platform.bing.com/recognize"

    self.querystring = {
      'version':   '3.0',
      'requestid': '{request_id}',
      'appid':     'D4D52672-91D7-4C74-8AD8-42B1D98141A5',
      'format':    'json',
      'locale':    self.lang,
      'device.os': 'ros',
      'scenarios': 'ulm',
      'instanceid': str(self.instance_id)
    }

    self.headers = {
        'content-type': "audio/wav; samplerate={actual_rate}; sourcerate={actual_rate}; trustsourcerate=true",
        'authorization': "{token}",
        'cache-control': "no-cache"
    }

    # start ROS node
    rospy.init_node('msspeech')

    # Request authorization token
    self.oauth = Authorization("ros-msspeech-node", _api_key)

    # configure ROS settings
    rospy.on_shutdown(self.shutdown)
    self.pub_speech = rospy.Publisher('speech', SpeechStamped, queue_size=10)
    self.srv_start = rospy.Service('~start', Empty, self.start)
    self.srv_stop = rospy.Service('~stop', Empty, self.stop)

    self.dur_threshold = rospy.get_param('~dur_threshold', 0.25)

    # run speech recognition
    self.started = True
    self.record_thread = threading.Thread(target=self.do_record, args=())
    self.record_thread.start()

    # recognition threads list
    self.lock = threading.Lock()
    self.threads_list = []

  def start(self, req):
    """Start speech recognition"""
    if not self.started:
      self.started = True
      if not self.record_thread.is_alive():
        self.record_thread = threading.Thread(
          target=self.do_record, args=()
        )
        self.record_thread.start()
      rospy.loginfo("msspeech recognizer started")
    else:
      rospy.loginfo("msspeech is already running")
    return EmptyResponse()

  def stop(self, req):
    """Stop speech recognition"""
    if self.started:
        self.started = False
        if self.record_thread.is_alive():
            os.killpg(os.getpgid(self.sox_p.pid), signal.SIGTERM)
            self.record_thread.join()
        rospy.loginfo("msspeech recognizer stopped")
    else:
        rospy.loginfo("msspeech is already stopped")
    return EmptyResponse()

  def shutdown(self):
    """Stop all system process before killing node"""
    self.started = False
    if self.record_thread.is_alive():
      os.killpg(os.getpgid(self.sox_p.pid), signal.SIGTERM)
      self.record_thread.join()
    self.srv_start.shutdown()
    self.srv_stop.shutdown()

  def do_record(self):
    """Do speech recognition"""
    while self.started:
      with tempfile.NamedTemporaryFile(prefix='gspeech', suffix='.wav', delete=False) as tmpfile:
        temp_file_name = tmpfile.name

      self.soxconv_args = shlex.split(self.soxconv_cmd.format(filename=temp_file_name))
      self.length_args = shlex.split(self.length_cmd.format(filename=temp_file_name))
      self.rate_args = shlex.split(self.rate_cmd.format(filename=temp_file_name))

      self.sox_p = subprocess.Popen(self.sox_args, stdout=subprocess.PIPE, preexec_fn=os.setsid)
      soxconv_out = subprocess.Popen(self.soxconv_args, stdin=self.sox_p.stdout, stdout=subprocess.PIPE, preexec_fn=os.setsid).communicate()[0]
      self.sox_p.stdout.close()

      end_time = rospy.Time.now()
      audio_len, _dummy_err = subprocess.Popen(self.length_args, stdout=subprocess.PIPE, preexec_fn=os.setsid).communicate()
      start_time = end_time - rospy.Duration(float(audio_len.strip()))

      if float(audio_len) < self.dur_threshold:
        rospy.logwarn("Recorded audio is too short ({}s < {}s). Ignoring".format(float(audio_len), self.dur_threshold))
        continue

      actual_rate, _dummy_err = subprocess.Popen(self.rate_args, stdout=subprocess.PIPE, preexec_fn=os.setsid).communicate()

      th = threading.Thread(
        target=self.do_recognition,
        args=(temp_file_name, int(actual_rate.strip()), start_time, end_time)
      )

      # lock
      self.lock.acquire()
      # modify the list
      try:
        self.threads_list.append(th)
      finally:
        # release
        self.lock.release()

      # start the thread
      self.threads_list[-1].start()

  def do_recognition(self, fname, actual_rate, start_time, end_time):
    request_id = uuid.uuid4()
    self.querystring['requestid'] = self.querystring['requestid'].format(request_id = request_id)
    self.headers['content-type'] = self.headers['content-type'].format(actual_rate = actual_rate)

    # Since token is updated automatically it should always be valid
    self.headers['authorization'] = self.headers['authorization'].format(token = self.oauth.token)

    response = requests.request("POST",
      self.url, # "http://httpbin.org/post",
      headers = self.headers,
      params  = self.querystring,
      data    = open(fname, 'rb')
    )

    os.unlink(fname)

    # print(response.text)

    confidence = 0.0

    if response.status_code == 200 and  response.json()['header']['status'] == 'success':
      a = response.json()
      if 'lexical' in a['results'][0]:
        # text = a['results'][0]['lexical']
        text = a['results'][0]['name']
        rospy.loginfo("text: {}".format(text))
      if 'confidence' in a['results'][0]:
        confidence = float(a['results'][0]['confidence'])
        confidence = confidence * 100
        rospy.loginfo("confidence: {}".format(confidence))

      msg = SpeechStamped()
      msg.header.stamp = start_time
      msg.header.frame_id = "human_frame"
      msg.duration = end_time - start_time
      msg.text = text
      msg.confidence = confidence
      self.pub_speech.publish(msg)

    # lock
    self.lock.acquire()
    try:
      # modify the list
      self.threads_list.remove(threading.currentThread())
    finally:
      # release
      self.lock.release()

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
  print "rosrun gspeech msspeech.py <SUBSCRIPTION_KEY> [LANG=en-us]"

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

  speech = MSSpeech(api_key, lang)

  rospy.spin()

if __name__ == '__main__':
  try:
    main()
  except rospy.ROSInterruptException:
    pass
  except KeyboardInterrupt:
    sys.exit(0)
