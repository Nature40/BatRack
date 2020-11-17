import logging
import datetime
import time
import threading
import wave
import copy
from collections import defaultdict

import numpy as np
import RPi.GPIO as GPIO
import pyaudio
import mysql.connector as mariadb
from numpy_ringbuffer import RingBuffer


logger = logging.getLogger(__name__)


class AbstractSensor:
    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def clean_up(self):
        raise NotImplementedError

    def get_status(self) -> dict:
        raise NotImplementedError


class CameraLightController(AbstractSensor):
    def __init__(self, led_pin):
        self.led_pin = led_pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)
        self.triggered_events_since_last_status = 0
        self.current_state = False

    def start(self):
        logger.info("Start camera and light controller")
        self.current_state = True
        self.__start_led()
        self.__start_camera()

    def stop(self):
        self.current_state = False
        self.__stop_led()
        self.__stop_camera()

    def clean_up(self):
        """
        clean up the GPIO state and close the connection
        :return:
        """
        GPIO.output(self.led_pin, GPIO.LOW)
        GPIO.cleanup()

    def get_status(self) -> dict:
        """
        delivers some nice information about the audio sensor since the last call
        :return:
        """
        return_values = {
            "trigger events": self.triggered_events_since_last_status, "is on": self.current_state}
        self.triggered_events_since_last_status = 0
        return return_values

    @staticmethod
    def __start_camera():
        """
        start the camera via file trigger to RPi_Cam_Web_Interface
        :return:
        """
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("1")

    @staticmethod
    def __stop_camera():
        """
        stop the camera via file trigger to RPi_Cam_Web_Interface
        :return:
        """
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("0")

    def __start_led(self):
        """
        start the led spot via GPIO
        :return:
        """
        GPIO.output(self.led_pin, GPIO.HIGH)

    def __stop_led(self):
        """
        stop the led spot via GPIO
        :return:
        """
        GPIO.output(self.led_pin, GPIO.LOW)


class Audio(AbstractSensor):
    def __init__(self,
                 data_folder,
                 threshold_dbfs,
                 highpass_frequency,
                 ring_buffer_length_in_sec,
                 audio_split,
                 min_seconds_follow_up_recording,
                 debug_on,
                 trigger_system,
                 silence_time,
                 noise_time):
        self.data_folder = data_folder
        self.threshold_dbfs = threshold_dbfs
        self.audio_split = audio_split
        self.trigger_system = trigger_system
        self.min_seconds_follow_up_recording = min_seconds_follow_up_recording

        self.pa = pyaudio.PyAudio()
        self.sampling_rate = 250000
        self.max_int_16 = 32767
        self.channels = 1
        self.format = pyaudio.paInt16
        self.input_block_time = 0.05
        self.input_frames_per_block = int(
            self.sampling_rate * self.input_block_time)
        self.silence_time = silence_time
        # if we have longer that this many blocks silence, it's a new sequence
        self.silence_blocks = self.silence_time / self.input_block_time
        # if the noise was longer than this many blocks, it's noise
        self.noise_time = noise_time
        self.max_tap_blocks = self.noise_time / self.input_block_time
        self.blocks_per_sec = self.sampling_rate / self.input_frames_per_block
        self.debug_on = debug_on

        self.filter_min_hz = highpass_frequency
        self.freq_bins_hz = np.arange((self.input_frames_per_block / 2) + 1) / (
            self.input_frames_per_block / float(self.sampling_rate))
        self.window_function_dbfs_max = np.sum(
            self.input_frames_per_block) / 2.0

        self.ring_buffer_length_in_sec = ring_buffer_length_in_sec
        self.ring_buffer = RingBuffer(
            int(self.ring_buffer_length_in_sec * self.blocks_per_sec), dtype=np.str)

        self.stream = self.__open_mic_stream()

        self.current_start_time_str: str = ""
        self.current_trigger_time: int = 0
        self.frames = []
        self.recording_thread: threading = None
        self.recording_thread_stopped = False
        self.noisy_count = 0
        self.quiet_count = 0
        self.pings = 0
        self.audio_recording = False
        self.trigger_events_since_last_status = 0
        self.use_trigger = False
        self.running = False
        self.trigger_from_extern = False

    def start(self, use_trigger: bool = False):
        """
        start audio recording if the microphone should be used
        :param use_trigger:
        :return:
        """
        if not self.running:
            logger.info("Start audio sensor")
            self.__start_new_file()
            self.__record(use_trigger)
        else:
            if not use_trigger and self.use_trigger:
                self.trigger_from_extern = True

    def stop(self, use_trigger: bool = False):
        """
        stop audio recording if the microphone should be used and start writing the audio to filesystem
        :return:
        """
        if self.running:
            if use_trigger == self.use_trigger:
                self.__stop_record()
                self.running = False
            elif not use_trigger and self.use_trigger:
                self.trigger_from_extern = False

    def clean_up(self):
        """
        stops the stream and terminates PyAudio. Additional it stops the recording first
        :return:
        """
        self.__stop_record()
        self.running = False
        self.trigger_from_extern = False
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()

    def get_status(self) -> dict:
        """
        delivers some nice information about the audio sensor since the last call
        :return:
        """
        return_values = {
            "trigger events": self.trigger_events_since_last_status, "use trigger": self.use_trigger}
        self.trigger_events_since_last_status = 0
        return return_values

    def __find_input_device(self) -> int:
        """
        searches for a microphone and returns the device number
        :return: the device id
        """
        device_index = None
        for i in range(self.pa.get_device_count()):
            dev_info = self.pa.get_device_info_by_index(i)
            logger.debug(f"Device {i}: {dev_info['name']}")

            for keyword in ["mic", "input"]:
                if keyword in dev_info["name"].lower():
                    logger.info(
                        f"Found an input: device {i} - {dev_info['name']}")
                    device_index = i
                    return device_index

        if device_index is None:
            logger.info(
                "No preferred input found; using default input device.")

    def __open_mic_stream(self):
        """
        open a PyAudio stream for the found device number and return the stream
        :return:
        """
        device_index = self.__find_input_device()

        stream = self.pa.open(format=self.format,
                              channels=self.channels,
                              rate=self.sampling_rate,
                              input=True,
                              input_device_index=device_index,
                              frames_per_buffer=self.input_frames_per_block)

        return stream

    def __start_new_file(self):
        """
        Save the current time for the filename and empty the frames to have a clear new state
        :return:
        """
        self.current_start_time_str = datetime.datetime.now().strftime("%Y-%m-%dT%H_%M_%S")
        self.current_trigger_time = time.time()
        self.frames = []

    def __save(self):
        """
        store the last recorded audio to the filesystem and clears the list of frames
        :return:
        """
        logger.info(f"len of frames: {len(self.frames)}")
        logger.info(
            "file name: {self.data_folder}{self.current_start_time_str}.wav")
        wave_file = wave.open(
            self.data_folder + self.current_start_time_str + ".wav", 'wb')
        wave_file.setnchannels(self.channels)
        wave_file.setsampwidth(self.pa.get_sample_size(self.format))
        wave_file.setframerate(self.sampling_rate)
        wave_file.writeframes(b''.join(self.frames))
        wave_file.close()
        self.__start_new_file()

    def __read_frame(self, use_overflow_exception=False):
        return self.stream.read(self.input_frames_per_block, exception_on_overflow=use_overflow_exception)

    def __observe_and_process_audio_stream(self, use_trigger: bool):
        """
        observes the audio stream continuous if no trigger is used or process every frame for an trigger and
        store frames only in case of a trigger.
        This method is supposed to be use in a separate thread so it checks every time if the thread should be stopped.
        Additionally it checks if the current audio dump is longer than the expected chunk and has to be splitted
        :param use_trigger: decides if the recording is continuous or triggered by the audio itself
        :return:
        """
        self.use_trigger = use_trigger
        while True:
            if self.recording_thread_stopped:
                self.__save()
                return
            if self.__is_time_for_audio_split():
                logger.info("doing audio split")
                self.__save()
            if use_trigger:
                frame = self.__read_frame()
                if self.trigger_from_extern:
                    self.frames.append(frame)
                else:
                    if self.__check_trigger_state(frame):
                        self.frames.append(frame)
            else:
                self.frames.append(self.__read_frame())

    def __check_trigger_state(self, frame) -> bool:
        """
        checks for the given frame if a trigger is present
        :param frame: the frame to check
        :return: the status of audio recording
        """
        spectrum = self.__exec_fft(frame)
        peak_db = self.__get_peak_db(spectrum)
        if self.__check_signal_for_threshold(peak_db):
            # noisy block
            self.quiet_count = 0
            self.noisy_count += 1
        else:
            # quiet block.
            if 1 <= self.noisy_count <= self.max_tap_blocks:
                self.pings += 1
                logger.info("ping")
                self.current_trigger_time = time.time()
            if self.pings >= 2 and not self.audio_recording:
                logger.info("audio_recording started")
                self.trigger_events_since_last_status += 1
                self.audio_recording = True
                self.trigger_system.start_sequence_audio()
            if self.quiet_count > self.silence_blocks and self.audio_recording:
                if time.time() > self.current_trigger_time + self.min_seconds_follow_up_recording:
                    self.pings = 0
                    self.audio_recording = False
                    self.trigger_system.stop_sequence_audio()
                    self.__save()
            self.noisy_count = 0
            self.quiet_count += 1
            return self.audio_recording

    def __record(self, use_trigger):
        """
        Creates and starts a thread to observe the audio stream
        :param use_trigger: decides if the recording is continuous or triggered by the audio itself
        :return:
        """
        self.recording_thread = threading.Thread(
            target=self.__observe_and_process_audio_stream, args=(use_trigger, ))
        self.recording_thread.start()

    def __stop_record(self):
        """
        set the flag for stopping the recording to True and termites the thread
        :return:
        """
        self.recording_thread_stopped = True

    def __exec_fft(self, signal) -> np.fft.rfft:
        """
        execute a fft for a given signal and cuts the the frequencies below self.filter_min_hz
        and return the resulting spectrum
        :param signal: givem signal to process the fft function
        :return:
        """
        data_int16 = np.frombuffer(signal, dtype=np.int16)
        spectrum = np.fft.rfft(data_int16)
        spectrum[self.freq_bins_hz < self.filter_min_hz] = 0.000000001
        return spectrum

    def __get_peak_db(self, spectrum: np.fft) -> int:
        """
        returns the maximum db of the given spectrum
        :param spectrum:
        :return:
        """
        dbfs_spectrum = 20 * \
            np.log10(np.abs(spectrum) /
                     max([self.window_function_dbfs_max, 1]))
        bin_peak_index = dbfs_spectrum.argmax()
        peak_db = dbfs_spectrum[bin_peak_index]
        peak_frequency_hz = bin_peak_index * \
            self.sampling_rate / self.input_frames_per_block
        logger.debug(f"Peak freq hz: {peak_frequency_hz} dBFS: {peak_db}")
        return peak_db

    def __check_signal_for_threshold(self, peak_db) -> bool:
        return peak_db > self.threshold_dbfs

    def __is_time_for_audio_split(self) -> bool:
        return time.time() > self.current_trigger_time + self.audio_split


class VHF(AbstractSensor):
    def __init__(self,
                 db_user,
                 db_password,
                 db_database,
                 vhf_frequencies,
                 frequency_range_for_vhf_frequency,
                 vhf_middle_frequency,
                 vhf_inactive_threshold,
                 time_between_vhf_pings_in_sec,
                 vhf_threshold,
                 vhf_duration,
                 observation_time_for_ping_in_sec,
                 debug_on,
                 trigger_system):
        self.db_user = db_user
        self.db_password = db_password
        self.db_database = db_database
        self.vhf_frequencies = vhf_frequencies
        self.frequency_range_for_vhf_frequency = frequency_range_for_vhf_frequency
        self.vhf_middle_frequency = vhf_middle_frequency
        self.time_between_vhf_pings_in_sec = time_between_vhf_pings_in_sec
        self.vhf_threshold = vhf_threshold
        self.trigger_system = trigger_system
        self.vhf_duration = vhf_duration
        self.observation_time_for_ping_in_sec = observation_time_for_ping_in_sec
        self.currently_active_vhf_frequencies = copy.deepcopy(
            self.vhf_frequencies)
        self.vhf_inactive_threshold = vhf_inactive_threshold
        self.stopped = False
        self.vhf_recording = False
        self.trigger_events_since_last_status = 0
        self.present_and_active_bats = []
        self.debug_on = debug_on

    def stop(self):
        self.stopped = True

    def start(self):
        logging.info("Start VHF sensor")
        check_signal_for_active_bats = threading.Thread(
            target=self.__check_vhf_signal_for_active_bats, args=())
        check_signal_for_active_bats.start()
        check_frequencies_for_inactivity = threading.Thread(
            target=self.__check_vhf_frequencies_for_inactivity, args=())
        check_frequencies_for_inactivity.start()

    def get_status(self):
        """
        delivers some nice information about the audio sensor since the last call
        :return:
        """
        present_and_inactive_bats = [
            x for x in self.vhf_frequencies if x not in self.currently_active_vhf_frequencies]
        absent_bats = [x for x in self.vhf_frequencies if
                       x not in (present_and_inactive_bats or self.present_and_active_bats)]
        return_values = {"running": not self.stopped,
                         "recording": self.vhf_recording,
                         "trigger events": self.trigger_events_since_last_status,
                         "bats present and inactive": present_and_inactive_bats,
                         "bats present and active": self.present_and_active_bats,
                         "bats absent": absent_bats,
                         "all observed frequencies": self.vhf_frequencies}
        self.trigger_events_since_last_status = 0
        return return_values

    def __query_for_frequency_and_signal_strength(self,
                                                  signal_threshold: int,
                                                  duration: float,
                                                  timestamp: datetime.datetime):
        """
        :param signal_threshold: signals must have a higher peak power than the threshold is
        :param duration: the duration of the signal must be less than this duration
        :param timestamp: the signal must be newer the the timestamp
        :return: returns the signal frequency and peak power for all matching signals
        """
        query = "SELECT signal_freq, max_signal FROM signals WHERE max_signal > %s AND duration < %s AND timestamp > %s"
        try:
            maria_db = mariadb.connect(
                user=self.db_user, password=self.db_password, database=self.db_database)
            cursor = maria_db.cursor()
            cursor.execute(query, (signal_threshold, duration, timestamp))
            return_values = cursor.fetchall()
            cursor.close()
            maria_db.close()
            return return_values
        except Exception as e:
            logger.info(f"Error for query: {query} with error: {e}")

    def __is_frequency_currently_active(self, frequency: int):
        """
        True if the given frequency is in the frequency range of currently active frequencies else False
        :param frequency: frequency to check
        :return: bool
        """
        for wanted_frequency in self.currently_active_vhf_frequencies:
            if wanted_frequency - self.frequency_range_for_vhf_frequency < \
                    (frequency + self.vhf_middle_frequency) / 1000 < \
                    wanted_frequency + self.frequency_range_for_vhf_frequency:
                return True
        return False

    def __is_frequency_a_bat_frequency(self, frequency: int):
        """
        True if the given frequency is in the frequency range of any of the potential bat frequencies else False
        :param frequency: frequency to check
        :return: bool
        """
        for wanted_frequency in self.vhf_frequencies:
            if wanted_frequency - self.frequency_range_for_vhf_frequency < \
                    (frequency + self.vhf_middle_frequency) / 1000 \
                    < wanted_frequency + self.frequency_range_for_vhf_frequency:
                return True
        return False

    def __get_matching_bat_frequency(self, frequency: int):
        """
        :param frequency: given frequency of signal
        :return: the matching frequency out of self.config.vhf_frequencies
        """
        for wanted_frequency in self.vhf_frequencies:
            if wanted_frequency - self.frequency_range_for_vhf_frequency < \
                    (frequency + self.vhf_middle_frequency) / 1000 < \
                    wanted_frequency + self.frequency_range_for_vhf_frequency:
                return wanted_frequency

    def __check_vhf_signal_for_active_bats(self):
        """
        an always running thread for the continuous check of all frequencies for activity
        if activity is detected the thread starts the recording
        """
        last_vhf_ping = datetime.datetime.now()
        while True:
            try:
                current_round_check = False
                if self.stopped:
                    break
                now = datetime.datetime.utcnow()
                start = time.time()
                query_results = self.__query_for_frequency_and_signal_strength(self.vhf_threshold,
                                                                               self.vhf_duration,
                                                                               now - datetime.timedelta(seconds=self.observation_time_for_ping_in_sec))
                logger.debug(
                    f"query check for active bats takes {time.time() - start}s")
                now = datetime.datetime.utcnow()
                self.present_and_active_bats = []
                for result in query_results:
                    frequency, signal_strength = result
                    real_frequency = (
                        frequency + self.vhf_middle_frequency) / 1000.0
                    logger.debug(
                        f"This frequency is detected: {real_frequency} with signal strength: {signal_strength}")
                    if self.__is_frequency_currently_active(frequency):
                        if self.__get_matching_bat_frequency(frequency) not in self.present_and_active_bats:
                            self.present_and_active_bats.append(
                                self.__get_matching_bat_frequency(frequency))
                        logger.debug(
                            f"This frequency is additional active: {real_frequency} with signal strength: {signal_strength}")
                        current_round_check = True
                        last_vhf_ping = now

                if current_round_check:
                    if not self.vhf_recording:
                        self.trigger_system.start_sequence_vhf()
                        self.vhf_recording = True
                        logger.info("vhf_recording start")
                        self.trigger_events_since_last_status += 1
                    time.sleep(1)
                else:
                    if self.vhf_recording and (now - last_vhf_ping) > datetime.timedelta(
                            seconds=self.observation_time_for_ping_in_sec):
                        self.trigger_system.stop_sequence_vhf()
                        self.vhf_recording = False
                        logger.info("vhf_recording stop")
                    time.sleep(0.2)
            except Exception as e:
                logger.info(f"Error in check_vhf_signal_for_active_bats: {e}")

    def __check_vhf_frequencies_for_inactivity(self):
        """
        an always running thread for continuous adding
        and removing frequencies from the currently active frequencies list
        """
        while True:
            try:
                if self.stopped:
                    break
                now = datetime.datetime.utcnow()
                start = time.time()
                query_results = self.__query_for_frequency_and_signal_strength(self.vhf_threshold,
                                                                               self.vhf_duration,
                                                                               now - datetime.timedelta(seconds=60))
                logger.debug(
                    f"query check for inactivity takes {time.time() - start}s")
                signals = defaultdict(list)
                for result in query_results:
                    frequency, signal_strength = result
                    if self.__is_frequency_a_bat_frequency(frequency):
                        signals[self.__get_matching_bat_frequency(
                            frequency)].append(signal_strength)

                for frequency in signals.keys():
                    if len(signals[frequency]) > 10 \
                            and np.std(signals[frequency]) < self.vhf_inactive_threshold  \
                            and frequency in self.currently_active_vhf_frequencies:
                        logger.info(f"remove frequency: {frequency}")
                        self.currently_active_vhf_frequencies.remove(frequency)
                    elif np.std(signals[frequency]) > self.vhf_inactive_threshold \
                            and frequency not in self.currently_active_vhf_frequencies:
                        if frequency not in self.currently_active_vhf_frequencies:
                            logger.info(f"add frequency: {frequency}")
                            self.currently_active_vhf_frequencies.append(
                                frequency)

                for frequency in self.vhf_frequencies:
                    if frequency not in signals.keys() and frequency not in self.currently_active_vhf_frequencies:
                        logger.info(f"add frequency: {frequency}")
                        self.currently_active_vhf_frequencies.append(frequency)
                time.sleep(10)
            except Exception as e:
                logger.warning(
                    f"Error in check_vhf_frequencies_for_inactivity: {e}")
