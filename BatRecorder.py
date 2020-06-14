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
from dateutil.parser import parse
from collections import defaultdict

class GroundTruther(object):
    def __init__(self, db_user, db_password, db_database, self_adapting = False, ring_buffer_length_in_sec=3, led_pin=14):
        self.stopped = False
        self.self_adapting = self_adapting
        self.led_pin = led_pin
        self.debug = False
        signal.signal(signal.SIGINT, self.signal_handler)

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

        ############################ CONFIG #########################################

        self.threshold_dbfs = 30
        self.highpass_frequency = 15000

        self.use_audio_trigger = False
        self.use_vhf_trigger = True
        self.use_camera = True
        self.use_microphone = False

        self.time_between_vhf_pings_in_sec = 0.8
        self.observation_time_for_ping_in_sec = (self.time_between_vhf_pings_in_sec * 5) + 0.1
        self.vhf_threshold = 80
        self.vhf_duration = 0.02
        self.vhf_frequencies = [150187, 150128, 150171, 150211]
        self.vhf_middle_frequency = 150125000

        self.vhf_inactive_threshold = 2

        ############################ CONFIG #########################################

        if self.use_microphone:
            self.stream = self.open_mic_stream()

        self.currently_active_vhf_frequencies = [150187, 150128, 150171, 150211]

        self.filter_min_hz = float(self.highpass_frequency)
        self.freq_bins_hz = np.arange((self.input_frames_per_block / 2) + 1) / (self.input_frames_per_block / float(self.sampling_rate))
        self.window_function_dbfs_max = np.sum(self.input_frames_per_block) / 2.0

        self.ring_buffer_length_in_sec = ring_buffer_length_in_sec
        self.ring_buffer = RingBuffer(int(self.ring_buffer_length_in_sec * self.blocks_per_sec), dtype=np.str)

        self.count_recorder = 0
        self.vhf_recording = False
        self.current_start_time = ""

        print("set gpio pin")
        sys.stdout.flush()
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)


        check_vhf_signal_for_active_bats_thread = threading.Thread(target=self.check_vhf_signal_for_active_bats,
                                                   args=(db_user, db_password, db_database, self.vhf_threshold))
        check_vhf_signal_for_active_bats_thread.start()
        check_vhf_frequencies_for_inactivity_thread = threading.Thread(target=self.check_vhf_frequencies_for_inactivity,
                                                   args=(db_user, db_password, db_database, self.vhf_threshold))
        check_vhf_frequencies_for_inactivity_thread.start()

    def __clean_up(self):
        self.stopped = True
        print("threads are stopped")
        self.stream.stop_stream()
        self.stream.close()
        print("stream is cleaned up")
        self.pa.terminate()
        print("pyaudio is cleaned up")
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)
        GPIO.cleanup()
        print("gpio is cleaned up")

    def signal_handler(self, sig = None, frame = None):
        print('You pressed Ctrl+C!')
        clean_up_thread = threading.Thread(target=self.__clean_up, args=())
        clean_up_thread.start()
        time.sleep(1)
        os._exit(0)

    def main_loop(self):
        if self.use_microphone:
            audio_recording = False
            pings = 0
            quietcount = 0
            noisycount = self.max_tap_blocks + 1
            errorcount = 0
            vhf_audio_recording = False
            while True:
                try:
                    signal = self.stream.read(self.input_frames_per_block, exception_on_overflow = False)
                    #self.ring_buffer.append(signal)
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
                            print("ping")
                            sys.stdout.flush()
                        if pings >= 2 and not audio_recording and self.use_audio_trigger:
                            self.startSequence()
                            print(str(time.time()) + " audio_recording started")
                            sys.stdout.flush()
                            audio_recording = True
                        if quietcount > self.silence_time:
                            pings = 0
                            if audio_recording:
                                self.stopSequence()
                                print(str(time.time()) + " audio_recording stoped")
                                sys.stdout.flush()
                                audio_recording = False
                        noisycount = 0
                        quietcount += 1
                        if self.self_adapting and quietcount > self.undersensitive:
                            # turn up the sensitivity
                            self.threshold_dbfs *= 0.9
                except IOError as e:
                    # dammit.
                    print("(%d) Error recording: %s" % (e))
                    self.signal_handler()
            else:
                while True:
                    time.sleep(1)



    ####################################################################################################################
    ################################################# Audio Functions ##################################################
    ####################################################################################################################
    def exec_fft(self, signal):
        data_int16 = np.frombuffer(signal, dtype=np.int16)
        spectrum = np.fft.rfft(data_int16)
        spectrum[self.freq_bins_hz < self.filter_min_hz] = 0.000000001
        return spectrum

    def get_peak_db(self, spectrum):
        dbfs_spectrum = 20 * np.log10(np.abs(spectrum) / max([self.window_function_dbfs_max, 1]))
        bin_peak_index = dbfs_spectrum.argmax()
        peak_db = dbfs_spectrum[bin_peak_index]
        if self.debug:
            peak_frequency_hz = bin_peak_index * self.sampling_rate / self.input_frames_per_block
            print('DEBUG: Peak freq hz: ' + str(peak_frequency_hz) + '   dBFS: ' + str(peak_db))
        return peak_db

    def stop(self):
        self.stream.close()

    def find_input_device(self):
        device_index = None
        for i in range( self.pa.get_device_count() ):
            devinfo = self.pa.get_device_info_by_index(i)
            print("Device %d: %s"%(i,devinfo["name"]))

            for keyword in ["mic", "input"]:
                if keyword in devinfo["name"].lower():
                    print("Found an input: device %d - %s"%(i,devinfo["name"]))
                    device_index = i
                    return device_index

        if device_index == None:
            print("No preferred input found; using default input device.")

        return device_index

    def open_mic_stream( self ):
        self.device_index = self.find_input_device()

        stream = self.pa.open(format = self.format,
                              channels = self.channels,
                              rate = self.sampling_rate,
                              input = True,
                              input_device_index = self.device_index,
                              frames_per_buffer = self.input_frames_per_block)

        return stream

    ####################################################################################################################
    ################################################# API to camera and light ##########################################
    ####################################################################################################################

    def __query_maria_db(self, db_user, db_password, db_database, signal_threshold, duration, timestamp):
        try:
            mariadb_connection = mariadb.connect(user=db_user, password=db_password, database=db_database)
            cursor = mariadb_connection.cursor()
            cursor.execute("SELECT signal_freq FROM signals WHERE max_signal > %s AND duration < %s AND timestamp > %s", (signal_threshold, duration, timestamp,))
            return_values = cursor.fetchall()
            cursor.close()
            mariadb_connection.close()
            return return_values
        except Exception as e:
            print("Error for query: " + str(query) + " with error: " + str(e))
            sys.stdout.flush()


    def __query_maria_db_2(self, db_user, db_password, db_database, signal_threshold, duration, timestamp):
        try:
            mariadb_connection = mariadb.connect(user=db_user, password=db_password, database=db_database)
            cursor = mariadb_connection.cursor()
            cursor.execute("SELECT signal_freq, max_signal FROM signals WHERE max_signal > %s AND duration < %s AND timestamp > %s", (signal_threshold, duration, timestamp))
            return_values = cursor.fetchall()
            cursor.close()
            mariadb_connection.close()
            return return_values
        except Exception as e:
            print("Error for query: " + str(query) + " with error: " + str(e))
            sys.stdout.flush()

    def __query_for_last_signals(self, db_user, db_password, db_database, signal_threshold, duration, timestamp):
        return self.__query_maria_db(db_user, db_password, db_database, signal_threshold, duration, timestamp)

    def __query_for_present_but_inactive_bats(self, db_user, db_password, db_database, signal_threshold, duration,
                                              timestamp):
        return self.__query_maria_db_2(db_user, db_password, db_database, signal_threshold, duration, timestamp)

    def __check_frequency_for_condition(self, frequency, condition):
        for wanted_frequency in condition:
            if wanted_frequency - 8 < ((int(frequency) + self.vhf_middle_frequency) / 1000) < wanted_frequency + 8:
                return True
        return False

    def __is_frequency_currently_active(self, frequency):
        for wanted_frequency in self.currently_active_vhf_frequencies:
            if wanted_frequency - 8 < ((int(frequency) + self.vhf_middle_frequency) / 1000) < wanted_frequency + 8:
                return True
        return False

    def __is_frequency_a_bat_frequency(self, frequency):
        for wanted_frequency in self.vhf_frequencies:
            if wanted_frequency - 8 < ((int(frequency) + self.vhf_middle_frequency) / 1000) < wanted_frequency + 8:
                return True
        return False

    def __get_matching_bat_frequency(self, frequency):
        for wanted_frequency in self.vhf_frequencies:
            if wanted_frequency - 8 < ((frequency + self.vhf_middle_frequency) / 1000) < wanted_frequency + 8:
                return wanted_frequency

    def check_vhf_signal_for_active_bats(self, db_user, db_password, db_database, signal_threshold):
        sys.stdout.flush()
        last_vhf_ping = datetime.datetime.now()
        while True:
            try:
                current_round_check = False
                if self.stopped:
                    break
                now = datetime.datetime.utcnow()
                query_results = self.__query_for_last_signals(db_user, db_password, db_database, signal_threshold,
                                                              self.vhf_duration, now - datetime.timedelta(
                                                              seconds=self.observation_time_for_ping_in_sec))
                now = datetime.datetime.utcnow()
                for result in query_results:
                    frequency = result[0]
                    if self.__is_frequency_currently_active(frequency):
                        current_round_check = True
                        last_vhf_ping = now

                if current_round_check:
                    if not self.vhf_recording and self.use_vhf_trigger:
                        self.startSequence()
                        self.vhf_recording = True
                        print(str(time.time()) + " vhf_recording start")
                        sys.stdout.flush()
                    time.sleep(1)
                else:
                    if self.vhf_recording and (now - last_vhf_ping) > datetime.timedelta(seconds=self.observation_time_for_ping_in_sec):
                        self.stopSequence()
                        self.vhf_recording = False
                        print(str(time.time()) + " vhf_recording stop")
                        sys.stdout.flush()
                    time.sleep(0.2)
            except Exception as e:
                print("Error in check_vhf_signal_for_active_bats: " + str(e))
                sys.stdout.flush()

    def check_vhf_frequencies_for_inactivity(self, db_user, db_password, db_database, signal_threshold):
        sys.stdout.flush()
        while True:
            try:
                if self.stopped:
                    break
                now = datetime.datetime.utcnow()
                query_results = self.__query_for_present_but_inactive_bats(db_user, db_password, db_database, signal_threshold,
                                                              self.vhf_duration,
                                                              now - datetime.timedelta(
                                                                  seconds=60))

                signals = defaultdict(list)
                for result in query_results:
                    frequency, signal_strength = result
                    sys.stdout.flush()
                    if self.__is_frequency_a_bat_frequency(frequency):
                        signals[self.__get_matching_bat_frequency(frequency)].append(signal_strength)

                sys.stdout.flush()

                for frequency in signals.keys():
                    sys.stdout.flush()
                    if len(signals[frequency]) > 10 and np.std(signals[frequency]) < self.vhf_inactive_threshold and frequency in self.currently_active_vhf_frequencies:
                        print(str(time.time()) + " remove frequency: " + str(frequency))
                        sys.stdout.flush()
                        self.currently_active_vhf_frequencies.remove(frequency)
                    elif np.std(signals[frequency]) > self.vhf_inactive_threshold and frequency not in self.currently_active_vhf_frequencies:
                        if frequency not in self.currently_active_vhf_frequencies:
                            print(str(time.time()) + " add frequency: " + str(frequency))
                            sys.stdout.flush()
                            self.currently_active_vhf_frequencies.append(frequency)

                for frequency in self.vhf_frequencies:
                    if frequency not in signals.keys() and frequency not in self.currently_active_vhf_frequencies:
                        print(str(time.time()) + " add frequency: " + str(frequency))
                        sys.stdout.flush()
                        self.currently_active_vhf_frequencies.append(frequency)
                time.sleep(10)
            except Exception as e:
                print("Error in check_vhf_frequencies_for_inactivity: " + str(e))
                sys.stdout.flush()

    def save_audio(self):
        wavefile = wave.open(self.current_start_time + ".wav", 'wb')
        wavefile.setnchannels(self.channels)
        wavefile.setsampwidth(self.pa.get_sample_size(self.format))
        wavefile.setframerate(self.sampling_rate)
        wavefile.writeframes(b''.join(self.frames))
        wavefile.close()

    def __check_recorder_at_start(self):
        self.count_recorder += 1
        print("start " + str(self.count_recorder))
        return self.count_recorder == 1

    def __check_recorder_at_stop(self):
        self.count_recorder -= 1
        print("stop " + str(self.count_recorder))
        return self.count_recorder == 0

    def __startAudio(self):
        if self.use_microphone:
            self.frames = []

    def __stopAudio(self):
        if self.use_microphone:
            save_audio_thread = threading.Thread(target=self.save_audio, args=())
            save_audio_thread.start()

    def __startCamera(self):
        if self.use_camera:
            with open("/var/www/html/FIFO1", "w") as f:
                f.write("1")

    def __stopCamera(self):
        if self.use_camera:
            with open("/var/www/html/FIFO1", "w") as f:
                f.write("0")

    def __startLed(self):
        if self.use_camera:
            GPIO.output(self.led_pin, GPIO.HIGH)

    def __stopLed(self):
        if self.use_camera:
            GPIO.output(self.led_pin, GPIO.LOW)

    def startSequence(self):
        if self.__check_recorder_at_start():
            print("start recording!")
            self.current_start_time = datetime.datetime.now().strftime("%Y-%m-%dT%H_%M_%S")
            start_audio_thread = threading.Thread(target=self.__startAudio, args=())
            start_audio_thread.start()
            start_led_thread = threading.Thread(target=self.__startLed, args=())
            start_led_thread.start()
            start_camera_thread = threading.Thread(target=self.__startCamera, args=())
            start_camera_thread.start()

    def stopSequence(self):
        if self.__check_recorder_at_stop():
            print("stop recording")
            stop_audio_thread = threading.Thread(target=self.__stopAudio, args=())
            stop_audio_thread.start()
            stop_led_thread = threading.Thread(target=self.__stopLed, args=())
            stop_led_thread.start()
            stop_camera_thread = threading.Thread(target=self.__stopCamera, args=())
            stop_camera_thread.start()


if __name__ == "__main__":
    groundTruther = GroundTruther("pi", "natur", "rteu")
    groundTruther.main_loop()

