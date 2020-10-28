import signal
import sys
import os
import wave
import time
import RPi.GPIO as GPIO
import threading
import numpy as np
from numpy_ringbuffer import RingBuffer
import os.path
import mysql.connector as mariadb

from collections import defaultdict
from configparser import ConfigParser
import copy
import json
import Sensors.Audio as AudioSensor
import Sensors.CameraLightController as CameraLightController

class BatRack(object):
    def __init__(self, db_user: str, db_password: str, db_database: str,
                 self_adapting: bool = False, ring_buffer_length_in_sec: int = 3):
        self.stopped = False
        self.self_adapting = self_adapting

        self.debug_on = False
        signal.signal(signal.SIGINT, self.signal_handler)

        self.db_user = db_user
        self.db_password = db_password
        self.db_database = db_database


        config_object = ConfigParser()
        config_object.read("/boot/BatRecorder.conf")
        self.config = config_object["CONFIG"]

        ######### get all config parameter #######################
        self.led_pin = self.__get_int_from_config("led_pin")

        self.camera_light_controller = CameraLightController(self.led_pin)

        self.threshold_dbfs = self.__get_int_from_config("threshold_dbfs")
        self.highpass_frequency = self.__get_int_from_config("highpass_frequency")

        self.audio_sensor = AudioSensor(self.threshold_dbfs, self.highpass_frequency, self.debug_on)

        self.use_audio_trigger = self.__get_bool_from_config("use_audio_trigger")
        self.use_vhf_trigger = self.__get_bool_from_config("use_vhf_trigger")
        self.use_camera = self.__get_bool_from_config("use_camera")
        self.use_microphone = self.__get_bool_from_config("use_microphone")
        self.run_continous = self.__get_bool_from_config("run_continous")

        self.time_between_vhf_pings_in_sec = self.__get_float_from_config("time_between_vhf_pings_in_sec")

        self.vhf_threshold = self.__get_int_from_config("vhf_threshold")
        self.vhf_duration = self.__get_float_from_config("vhf_duration")
        self.vhf_frequencies = self.__get_list_from_config("vhf_frequencies")
        self.vhf_middle_frequency = self.__get_int_from_config("vhf_middle_frequency")

        self.vhf_inactive_threshold = self.__get_int_from_config("vhf_inactive_threshold")

        self.start_time = datetime.time(self.__get_int_from_config("start_time_h"),
                                        self.__get_int_from_config("start_time_m"),
                                        self.__get_int_from_config("start_time_s"))
        self.end_time = datetime.time(self.__get_int_from_config("end_time_h"),
                                      self.__get_int_from_config("end_time_m"),
                                      self.__get_int_from_config("end_time_s"))

        self.min_seconds_for_audio_recording = self.__get_int_from_config("min_seconds_for_audio_recording")

        self.frequency_range_for_vhf_frequency = self.__get_int_from_config("frequency_range_for_vhf_frequency")

        self.audio_split = self.__get_int_from_config("audio_split")

        self.waiting_time_after_start = self.__get_int_from_config("waiting_time_after_start")


        self.observation_time_for_ping_in_sec = self.time_between_vhf_pings_in_sec * 5 + 0.1
        self.currently_active_vhf_frequencies = copy.deepcopy(self.vhf_frequencies)



        self.ring_buffer_length_in_sec = ring_buffer_length_in_sec
        self.ring_buffer = RingBuffer(int(self.ring_buffer_length_in_sec * self.blocks_per_sec), dtype=np.str)

        self.count_recorder = 0
        self.vhf_recording = False
        self.current_start_time = ""

        self.print_message("set gpio pin", False)
        sys.stdout.flush()
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)

        if self.use_vhf_trigger:
            check_vhf_signal_for_active_bats_thread = threading.Thread(target=self.check_vhf_signal_for_active_bats,
                                                   args=())
            check_vhf_signal_for_active_bats_thread.start()
            check_vhf_frequencies_for_inactivity_thread = threading.Thread(target=self.check_vhf_frequencies_for_inactivity,
                                                   args=())
            check_vhf_frequencies_for_inactivity_thread.start()

        self.main_loop()

    ######################################## helper functions ##########################################################

    def __clean_up(self):
        '''stops all streams, clean up state and set the gpio to low'''
        self.stopped = True
        self.print_message("threads are stopped", False)
        self.print_message("stream is cleaned up", False)
        self.print_message("pyaudio is cleaned up", False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)
        GPIO.cleanup()
        self.print_message("gpio is cleaned up", False)

    def time_in_range(self, start: datetime.time, end: datetime.time, x: datetime.time):
        '''Return true if x is in the range [start, end]'''
        if start <= end:
            return start <= x <= end
        else:
            return start <= x or x <= end

    def signal_handler(self, sig=None, frame=None):
        '''cleans all states and streams up and terminates the process'''
        self.print_message("You pressed Ctrl+C!", False)
        clean_up_thread = threading.Thread(target=self.__clean_up, args=())
        clean_up_thread.start()
        time.sleep(1)
        os._exit(0)


    def get_time(self):
        '''returns the current datetime as string'''
        return str(datetime.datetime.now())

    def print_message(self, message: str, is_debug: bool = False):
        '''helper function for consistent output'''
        if is_debug and self.debug_on:
            print("DEBUG: " + self.get_time() + message)
        if not is_debug:
            print(self.get_time() + " " + message)
        sys.stdout.flush()

    ############################################# main loop ############################################################

    def main_loop(self):
        '''
        get the signals from the microphone and process the audio in case the microphone is in use
        else it is only a aways running dummy loop
        '''
        if self.use_microphone:
            if self.use_microphone:
                time.sleep(self.waiting_time_after_start)
                audio_recording = False
                pings = 0
                quietcount = 0
                noisycount = self.max_tap_blocks + 1
                vhf_audio_recording = False
                start_time_audio = time.time()
                self.frames = []
                while True:
                    try:
                        if self.run_continous:
                            if audio_recording:
                                self.frames.append(signal)
                                if not self.time_in_range(self.start_time, self.end_time,
                                                          datetime.datetime.now().time()):
                                    self.stopSequence()
                                    self.print_message("audio_recording stopped")
                                    audio_recording = False
                                if time.time() > start_time_audio + self.audio_split:
                                    self.print_message("doing audio split")
                                    frames_to_store = self.frames
                                    self.frames = []
                                    self.__stopAudio(frames_to_store)
                                    self.__startAudio()
                                    start_time_audio = time.time()
                                    sys.stderr.flush()

                            else:
                                if self.time_in_range(self.start_time, self.end_time, datetime.datetime.now().time()):
                                    self.startSequence()
                                    self.print_message("audio_recording started")
                                    audio_recording = True
                                    start_time_audio = time.time()
                        else:
                            signal = self.stream.read(self.input_frames_per_block, exception_on_overflow=False)
                            # self.ring_buffer.append(signal)
                            spectrum = self.exec_fft(signal)
                            peak_db = self.get_peak_db(spectrum)

                            if audio_recording:
                                self.frames.append(signal)
                            if not self.use_audio_trigger:
                                if self.vhf_recording and not vhf_audio_recording:
                                    vhf_audio_recording = True
                                elif self.vhf_recording and vhf_audio_recording:
                                    self.frames.append(signal)
                                continue

                            if peak_db > self.threshold_dbfs:
                                # noisy block
                                quietcount = 0
                                noisycount += 1
                                if self.self_adapting and noisycount > self.oversensitive:
                                    # turn down the sensitivity
                                    self.threshold_dbfs *= 1.1
                            else:
                                # quiet block.
                                if 1 <= noisycount <= self.max_tap_blocks:
                                    pings += 1
                                    self.print_message("ping")
                                    start_time_audio = time.time()
                                if pings >= 2 and not audio_recording and self.use_audio_trigger:
                                    if self.time_in_range(self.start_time, self.end_time,
                                                          datetime.datetime.now().time()):
                                        self.startSequence()

                                        self.print_message("audio_recording started")
                                        audio_recording = True
                                        start_time_audio = time.time()
                                    else:
                                        self.print_message("it is not the time to listen")
                                if quietcount > self.silence_time:
                                    pings = 0
                                    if audio_recording:
                                        if time.time() > (start_time_audio + self.min_seconds_for_audio_recording):
                                            self.stopSequence()
                                            self.print_message("audio_recording stopped")
                                            audio_recording = False
                                if time.time() > start_time_audio + self.audio_split:
                                    self.print_message("doing audio split")
                                    frames_to_store = copy.deepcopy(self.frames)
                                    self.frames = []
                                    self.__stopAudio(frames_to_store)
                                    self.__startAudio()

                                noisycount = 0
                                quietcount += 1
                                if self.self_adapting and quietcount > self.undersensitive:
                                    # turn up the sensitivity
                                    self.threshold_dbfs *= 0.9

                    except IOError as e:
                        # dammit.
                        self.print_message("(%d) Error recording: %s" % (e))
                        self.__clean_up()
                else:
                    while True:
                        time.sleep(1)

    ######################################### API to camera, audio and light ###########################################

    def __check_recorder_at_start(self):
        '''
        increases the number of sensor which want to record at the time
        :return: if the recording should be started
        '''
        self.count_recorder += 1
        self.print_message("start {}".format(self.count_recorder), False)
        return self.count_recorder == 1

    def __check_recorder_at_stop(self):
        '''
        decreases the number of sensor which want to record at the time
        :return: if the recording should be stopped
        '''
        self.count_recorder -= 1
        self.print_message("stop {}".format(self.count_recorder), False)
        return self.count_recorder == 0





    def startSequence(self):
        '''start all parts of the system to record a bat'''
        if self.__check_recorder_at_start():
            self.print_message("start recording", False)
            self.__startAudio()
            self.camera_light_controller.start_led()
            self.camera_light_controller.start_camera()

    def stopSequence(self):
        '''stop all parts of the system which are used to record bats'''
        if self.__check_recorder_at_stop():
            self.print_message("stop recording", False)
            self.__stopAudio(self.frames)
            self.camera_light_controller.stop_led()
            self.camera_light_controller.stop_camera()

if __name__ == "__main__":
    batRecorder = BatRack("pi", "natur", "rteu")

