import pyaudio
import signal
import sys
import os
import numpy as np
import wave
import time
import picamera
import RPi.GPIO as GPIO
import subprocess
import threading
import datetime
import numpy as np
from numpy_ringbuffer import RingBuffer
import os.path
import shlex
import mysql.connector as mariadb
import mysql
import datetime
from dateutil.parser import parse
from collections import defaultdict
from configparser import ConfigParser
import copy
from typing import *

class BatRecorder(object):
    def __init__(self, db_user: str, db_password: str, db_database: str,
                 self_adapting: bool = False, ring_buffer_length_in_sec: int = 3):
        self.stopped = False
        self.self_adapting = self_adapting

        self.debug_on = False
        signal.signal(signal.SIGINT, self.signal_handler)

        self.db_user = db_user
        self.db_password = db_password
        self.db_database = db_database

        self.pa = pyaudio.PyAudio()
        self.sampling_rate = 250000
        self.max_int_16 = 32767
        self.channels = 1
        self.format = pyaudio.paInt16
        self.input_block_time = 0.05
        self.input_frames_per_block = int(self.sampling_rate * self.input_block_time)
        # if we have longer that this many blocks silence, it's a new sequence
        self.silence_time = 1.0 / self.input_block_time
        # if the noise was longer than this many blocks, it's noise
        self.max_tap_blocks = 0.15 / self.input_block_time
        # if we get this many noisy blocks in a row, increase the threshold
        self.oversensitive = 15.0 / self.input_block_time
        # if we get this many quiet blocks in a row, decrease the threshold
        self.undersensitive = 120.0 / self.input_block_time
        self.blocks_per_sec = self.sampling_rate / self.input_frames_per_block

        config_object = ConfigParser()
        config_object.read("/boot/BatRecorderConfig.conf")
        self.config = config_object["CONFIG"]

        self.led_pin = self.config.led_pin

        self.start_time = datetime.time(self.config.start_time_h, self.config.start_time_m, self.config.start_time_s)
        self.end_time = datetime.time(self.config.end_time_h, self.config.end_time_m, self.config.end_time_s)

        if self.config.use_microphone:
            self.stream = self.open_mic_stream()

        self.observation_time_for_ping_in_sec = (float(self.config.time_between_vhf_pings_in_sec) * 5) + 0.1
        self.currently_active_vhf_frequencies = copy.deepcopy(self.config.vhf_frequencies)

        self.filter_min_hz = float(self.config.highpass_frequency)
        self.freq_bins_hz = np.arange((self.input_frames_per_block / 2) + 1) / \
                            (self.input_frames_per_block / float(self.sampling_rate))
        self.window_function_dbfs_max = np.sum(self.input_frames_per_block) / 2.0

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
        self.stream.stop_stream()
        self.stream.close()
        self.print_message("stream is cleaned up", False)
        self.pa.terminate()
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
            print(self.get_time() + message)
        sys.stdout.flush()

    ############################################# main loop ############################################################

    def main_loop(self):
        '''
        get the signals from the microphone and process the audio in case the microphone is in use
        else it is only a aways running dummy loop
        '''
        if self.config.use_microphone:
            audio_recording = False
            pings = 0
            quietcount = 0
            noisycount = self.max_tap_blocks + 1
            vhf_audio_recording = False
            start_time_audio = 0
            while True:
                try:
                    signal = self.stream.read(self.input_frames_per_block, exception_on_overflow = False)
                    #self.ring_buffer.append(signal)
                    spectrum = self.exec_fft(signal)
                    peak_db = self.get_peak_db(spectrum)

                    if audio_recording:
                        self.frames.append(signal)
                    if not self.config.use_audio_trigger:
                        if self.vhf_recording and not vhf_audio_recording:
                            vhf_audio_recording = True
                        elif self.vhf_recording and vhf_audio_recording:
                            self.frames.append(signal)
                        continue

                    if peak_db > self.config.threshold_dbfs:
                        # noisy block
                        quietcount = 0
                        noisycount += 1
                        if self.self_adapting and noisycount > self.oversensitive:
                            # turn down the sensitivity
                            self.config.threshold_dbfs *= 1.1
                    else:
                        # quiet block.
                        if 1 <= noisycount <= self.max_tap_blocks:
                            # noisy block after quiet block => potential bat call
                            pings += 1
                            self.print_message("ping", False)
                        if pings >= 2 and not audio_recording and self.config.use_audio_trigger:
                            # notice second potential bat call => start the recording
                            if self.time_in_range(self.start_time, self.end_time, datetime.datetime.now().time()):
                                self.startSequence()
                                self.print_message("audio_recording started", False)
                                audio_recording = True
                                start_time_audio = time.time()
                            else:
                                self.print_message("it is not the time to listen", False)
                        # too much quiet time the bat seems to be flown away
                        if quietcount > self.silence_time:
                            pings = 0
                            if audio_recording:
                                if time.time() > start_time_audio + self.config.min_seconds_for_audio_recording:
                                    self.stopSequence()
                                    self.print_message("audio_recording stopped", False)
                                    audio_recording = False

                        if time.time() > start_time_audio + self.config.audio_split:
                            frames_to_store = copy.deepcopy(self.frames)
                            self.frames = []
                            self.__stopAudio(frames_to_store)
                            self.__startAudio()
                        noisycount = 0
                        quietcount += 1
                        if self.self_adapting and quietcount > self.undersensitive:
                            # turn up the sensitivity
                            self.config.threshold_dbfs *= 0.9
                except IOError as e:
                    # dammit.
                    self.print_message("Error recording: {}".format(e), False)
                    self.signal_handler()
            else:
                while True:
                    time.sleep(1)

    ################################################# Audio Functions ##################################################

    def exec_fft(self, signal):
        '''
        execute a fft for a given signal and cuts the the frequencies below self.filter_min_hz
        and return the resulting spectrum
        '''
        data_int16 = np.frombuffer(signal, dtype=np.int16)
        spectrum = np.fft.rfft(data_int16)
        spectrum[self.freq_bins_hz < self.filter_min_hz] = 0.000000001
        return spectrum

    def get_peak_db(self, spectrum: np.fft):
        '''returns the maximum db of the given spectrum'''
        dbfs_spectrum = 20 * np.log10(np.abs(spectrum) / max([self.window_function_dbfs_max, 1]))
        bin_peak_index = dbfs_spectrum.argmax()
        peak_db = dbfs_spectrum[bin_peak_index]
        if self.debug_on:
            peak_frequency_hz = bin_peak_index * self.sampling_rate / self.input_frames_per_block
            self.print_message("DEBUG: Peak freq hz: " + str(peak_frequency_hz) + "   dBFS: " + str(peak_db), True)
        return peak_db

    def stop(self):
        '''closes the audio stream'''
        self.stream.close()

    def find_input_device(self):
        '''searches for a microphone and returns the device number'''
        device_index = None
        for i in range( self.pa.get_device_count() ):
            dev_info = self.pa.get_device_info_by_index(i)
            self.print_message("Device {}: {}".format(i, dev_info["name"]), True)

            for keyword in ["mic", "input"]:
                if keyword in dev_info["name"].lower():
                    self.print_message("Found an input: device {} - {}".format(i, dev_info["name"]), True)
                    device_index = i
                    return device_index

        if device_index == None:
            self.print_message("No preferred input found; using default input device.", False)
            self.signal_handler()

        return device_index

    def open_mic_stream(self):
        '''open a PyAudio stream for the found device number and return the stream'''
        device_index = self.find_input_device()

        stream = self.pa.open(format = self.format,
                              channels = self.channels,
                              rate = self.sampling_rate,
                              input = True,
                              input_device_index = device_index,
                              frames_per_buffer = self.input_frames_per_block)

        return stream

    ######################################### check activity of bats ###################################################

    def __query_for_last_signals(self, signal_threshold: int, duration: float, timestamp: datetime.datetime):
        '''
        :param signal_threshold: signals must have a higher peak power than the threshold is
        :param duration: the duration of the signal must be less than this duration
        :param timestamp: the signal must be newer the the timestamp
        :return: returns the signal frequency for all matching signals
        '''
        query = "SELECT signal_freq FROM signals WHERE max_signal > %s AND duration < %s AND timestamp > %s"
        try:
            mariadb_connection = mariadb.connect(user=self.db_user, password=self.db_password, database=self.db_database)
            cursor = mariadb_connection.cursor()
            cursor.execute(query, (signal_threshold, duration, timestamp,))
            return_values = cursor.fetchall()
            cursor.close()
            mariadb_connection.close()
            return return_values
        except Exception as e:
            self.print_message("Error for query: {} with error: {}".format(query, e), False)

    def __query_for_present_but_inactive_bats(self,  signal_threshold: int, duration: float, timestamp: datetime.datetime):
        '''
        :param signal_threshold: signals must have a higher peak power than the threshold is
        :param duration: the duration of the signal must be less than this duration
        :param timestamp: the signal must be newer the the timestamp
        :return: returns the signal frequency and peak power for all matching signals
        '''
        query = "SELECT signal_freq, max_signal FROM signals WHERE max_signal > %s AND duration < %s AND timestamp > %s"
        try:
            mariadb_connection = mariadb.connect(user=self.db_user, password=self.db_password, database=self.db_database)
            cursor = mariadb_connection.cursor()
            cursor.execute(query, (signal_threshold, duration, timestamp))
            return_values = cursor.fetchall()
            cursor.close()
            mariadb_connection.close()
            return return_values
        except Exception as e:
            self.print_message("Error for query: {} with error: {}".format(query, e), False)

    def __is_frequency_currently_active(self, frequency: int):
        '''
        True if the given frequency is in the frequency range of currently active frequencies else False
        :param frequency: frequency to check
        :return: bool
        '''
        for wanted_frequency in self.currently_active_vhf_frequencies:
            if wanted_frequency - self.config.frequency_range_for_vhf_frequency < \
                    ((int(frequency) + self.config.vhf_middle_frequency) / 1000) < \
                    wanted_frequency + self.config.frequency_range_for_vhf_frequency:
                return True
        return False

    def __is_frequency_a_bat_frequency(self, frequency):
        '''
        True if the given frequency is in the frequency range of any of the potential bat frequencies else False
        :param frequency: frequency to check
        :return: bool
        '''
        for wanted_frequency in self.config.vhf_frequencies:
            if wanted_frequency - self.config.frequency_range_for_vhf_frequency < \
                    ((int(frequency) + self.config.vhf_middle_frequency) / 1000) \
                    < wanted_frequency + self.config.frequency_range_for_vhf_frequency:
                return True
        return False

    def __get_matching_bat_frequency(self, frequency):
        '''
        :param frequency: given frequency of signal
        :return: the matching frequency out of self.config.vhf_frequencies
        '''
        for wanted_frequency in self.config.vhf_frequencies:
            if wanted_frequency - self.config.frequency_range_for_vhf_frequency < \
                    ((frequency + self.config.vhf_middle_frequency) / 1000) < \
                    wanted_frequency + self.config.frequency_range_for_vhf_frequency:
                return wanted_frequency

    def check_vhf_signal_for_active_bats(self):
        '''
        an always running thread for the continuous check of all frequencies for activity
        if activity is detected the thread starts the recording
        '''
        last_vhf_ping = datetime.datetime.now()
        while True:
            try:
                current_round_check = False
                if self.stopped:
                    break
                now = datetime.datetime.utcnow()
                query_results = self.__query_for_last_signals(self.config.vhf_threshold, self.config.vhf_duration,
                                                              now - datetime.timedelta(
                                                              seconds=self.observation_time_for_ping_in_sec))
                now = datetime.datetime.utcnow()
                for result in query_results:
                    frequency = result[0]
                    if self.__is_frequency_currently_active(frequency):
                        current_round_check = True
                        last_vhf_ping = now

                if current_round_check:
                    if not self.vhf_recording and self.config.use_vhf_trigger:
                        self.startSequence()
                        self.vhf_recording = True
                        self.print_message("vhf_recording start", False)
                    time.sleep(1)
                else:
                    if self.vhf_recording and (now - last_vhf_ping) > datetime.timedelta(
                            seconds=self.observation_time_for_ping_in_sec):
                        self.stopSequence()
                        self.vhf_recording = False
                        self.print_message("vhf_recording stop", False)
                    time.sleep(0.2)
            except Exception as e:
                self.print_message("Error in check_vhf_signal_for_active_bats: {}".format(e), False)

    def check_vhf_frequencies_for_inactivity(self):
        '''
        an always running thread for continuous adding
        and removing frequencies from the currently active frequencies list
        '''
        sys.stdout.flush()
        while True:
            try:
                if self.stopped:
                    break
                now = datetime.datetime.utcnow()
                query_results = self.__query_for_present_but_inactive_bats(self.config.vhf_threshold,
                                                                           self.config.vhf_duration,
                                                                           now - datetime.timedelta(seconds=60))

                signals = defaultdict(list)
                for result in query_results:
                    frequency, signal_strength = result
                    sys.stdout.flush()
                    if self.__is_frequency_a_bat_frequency(frequency):
                        signals[self.__get_matching_bat_frequency(frequency)].append(signal_strength)

                for frequency in signals.keys():
                    sys.stdout.flush()
                    if len(signals[frequency]) > 10 \
                            and np.std(signals[frequency]) < self.config.vhf_inactive_threshold  \
                            and frequency in self.currently_active_vhf_frequencies:
                        self.print_message("remove frequency: {}".format(frequency), False)
                        self.currently_active_vhf_frequencies.remove(frequency)
                    elif np.std(signals[frequency]) > self.config.vhf_inactive_threshold \
                            and frequency not in self.currently_active_vhf_frequencies:
                        if frequency not in self.currently_active_vhf_frequencies:
                            self.print_message("add frequency: {}".format(frequency), False)
                            self.currently_active_vhf_frequencies.append(frequency)

                for frequency in self.config.vhf_frequencies:
                    if frequency not in signals.keys() and frequency not in self.currently_active_vhf_frequencies:
                        self.print_message("add frequency: {}".format(frequency), False)
                        self.currently_active_vhf_frequencies.append(frequency)
                time.sleep(10)
            except Exception as e:
                self.print_message("Error in check_vhf_frequencies_for_inactivity: ".format(e), False)

    ######################################### API to camera, audio and light ###########################################

    def save_audio(self, frames):
        '''store the last recorded audio to the filesystem'''
        wavefile = wave.open(self.current_start_time + ".wav", 'wb')
        wavefile.setnchannels(self.channels)
        wavefile.setsampwidth(self.pa.get_sample_size(self.format))
        wavefile.setframerate(self.sampling_rate)
        wavefile.writeframes(b''.join(frames))
        wavefile.close()

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

    def __startAudio(self):
        '''start audio recording if the microphone should be used'''
        if self.config.use_microphone:
            self.current_start_time = datetime.datetime.now().strftime("%Y-%m-%dT%H_%M_%S")
            self.frames = []

    def __stopAudio(self, frames):
        '''stop audio recording if the microphone should be used and start writing the audio to filesystem'''
        if self.config.use_microphone:
            save_audio_thread = threading.Thread(target=self.save_audio, args=(frames))
            save_audio_thread.start()

    def __startCamera(self):
        '''start the camera if the camera should be used'''
        if self.config.use_camera:
            with open("/var/www/html/FIFO1", "w") as f:
                f.write("1")

    def __stopCamera(self):
        '''stop the camera if the camera should be used'''
        if self.config.use_camera:
            with open("/var/www/html/FIFO1", "w") as f:
                f.write("0")

    def __startLed(self):
        '''start the led spot if the led spot should be used'''
        if self.config.use_camera:
            GPIO.output(self.led_pin, GPIO.HIGH)

    def __stopLed(self):
        '''stop the led spot if the led spot should be used'''
        if self.config.use_camera:
            GPIO.output(self.led_pin, GPIO.LOW)

    def startSequence(self):
        '''start all parts of the system to record a bat'''
        if self.__check_recorder_at_start():
            self.print_message("start recording", False)
            self.__startAudio()
            self.__startLed()
            self.__startCamera()

    def stopSequence(self):
        '''stop all parts of the system which are used to record bats'''
        if self.__check_recorder_at_stop():
            self.print_message("stop recording", False)
            self.__stopAudio(self.frames)
            self.__stopLed()
            self.__stopCamera()

if __name__ == "__main__":
    batRecorder = BatRecorder("pi", "natur", "rteu")

