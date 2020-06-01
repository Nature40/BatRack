import pyaudio
import signal
import sys
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

        self.stream = self.open_mic_stream()
        self.threshold_dbfs = 30
        self.count_recorder = 0

        self.highpass_frequency = 15000
        self.fft_highpass = True

        self.filter_min_hz = float(self.highpass_frequency)
        self.freq_bins_hz = np.arange((self.input_frames_per_block / 2) + 1) / (self.input_frames_per_block / float(self.sampling_rate))
        self.window_function_dbfs_max = np.sum(self.input_frames_per_block) / 2.0

        self.ring_buffer_length_in_sec = ring_buffer_length_in_sec
        self.ring_buffer = RingBuffer(int(self.ring_buffer_length_in_sec * self.blocks_per_sec), dtype=np.str)

        self.current_start_time = ""
        self.use_audio_trigger = True
        self.use_vhf_trigger = True
        self.vhf_recording = False

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)

        self.time_between_vhf_pings_in_sec = 0.8
        self.observation_time_for_ping_in_sec = (self.time_between_vhf_pings_in_sec * 2) + 0.1
        self.vhf_threshold = 80
        check_vhf_signal_thread = threading.Thread(target=self.check_vhf_signal, args=(self.vhf_threshold, db_user, db_password, db_database))
        check_vhf_signal_thread.start()

    def signal_handler(self, sig = None, frame = None):
        print('You pressed Ctrl+C!')
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()
        self.stopped = True
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)
        GPIO.cleanup()

    def listen(self):
        audio_recording = False
        pings = 0
        quietcount = 0
        noisycount = self.max_tap_blocks + 1
        errorcount = 0
        vhf_audio_recording = False
        while True:
            try:
                signal = self.stream.read(self.input_frames_per_block)
                #self.ring_buffer.append(signal)
                peak_db = 0
                start_time = 0
                spectrum = self.exec_fft(signal)
                peak_db = self.get_peak_db(spectrum)
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
                        #print("quiet")
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
                errorcount += 1
                print("(%d) Error recording: %s"%(errorcount,e))
                if errorcount > 100:
                    self.signal_handler()
    ####################################################################################################################
    ################################################# Audio Functions ##################################################
    ####################################################################################################################
    def exec_fft(self, signal):
        data_int16 = np.frombuffer(signal, dtype=np.int16)
        spectrum = np.fft.rfft(data_int16)
        if self.fft_highpass:
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

    def check_vhf_signal(self, threshold, db_user, db_password, db_database):
        vhf_recording = False
        last_vhf_ping = datetime.datetime.now()
        while True:
            if self.stopped:
                break
            #ToDo: Use correct sql statment
            mariadb_connection = mariadb.connect(user=db_user, password=db_password, database=db_database)
            cursor = mariadb_connection.cursor()
            cursor.execute("SELECT timestamp FROM signals WHERE max_signal>%s ORDER BY timestamp DESC LIMIT 1", (threshold,))
            date_time_str = cursor.fetchall()[0][0]
            cursor.close()
            mariadb_connection.close()
            last_timestamp = parse(date_time_str)
            now = datetime.datetime.utcnow()
            difference = now - last_timestamp
            if datetime.timedelta(seconds=0.0) < difference < datetime.timedelta(seconds=self.observation_time_for_ping_in_sec):
                last_vhf_ping = now
                if not vhf_recording and self.use_vhf_trigger:
                    self.startSequence()
                    vhf_recording = True
                    print(str(time.time()) + " vhf_recording start")
                    sys.stdout.flush()
                time.sleep(1)
            else:
                if vhf_recording and (now - last_vhf_ping) > datetime.timedelta(seconds=5):
                    self.stopSequence()
                    vhf_recording = False
                    print(str(time.time()) + " vhf_recording stop")
                    sys.stdout.flush()
                else:
                    print(difference)
                    sys.stdout.flush()
                time.sleep(0.2)

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
        self.frames = []

    def __stopAudio(self):
        save_audio_thread = threading.Thread(target=self.save_audio, args=())
        save_audio_thread.start()

    def __startCamera(self):
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("1")

    def __stopCamera(self):
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("0")

    def __startLed(self):
        GPIO.output(self.led_pin, GPIO.HIGH)

    def __stopLed(self):
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
    groundTruther.listen()

