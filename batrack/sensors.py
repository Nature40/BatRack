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
    def __init__(self, light_pin: int):
        self.light_pin = int(light_pin)
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.light_pin, GPIO.OUT)
        GPIO.output(self.light_pin, GPIO.LOW)
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
        GPIO.output(self.light_pin, GPIO.LOW)
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
        GPIO.output(self.light_pin, GPIO.HIGH)

    def __stop_led(self):
        """
        stop the led spot via GPIO
        :return:
        """
        GPIO.output(self.light_pin, GPIO.LOW)


class Audio(AbstractSensor):
    def __init__(self,
                 data_path,
                 trigger_system,
                 threshold_dbfs: int,
                 highpass_hz: int,
                 ring_buffer_len_s: float,
                 wave_export_len_s: float,
                 silence_duration_s: float,
                 noise_duration_s: float,
                 inter_recording_pause_s: float,
                 ):

        # instance variables
        self.data_path = data_path
        self.trigger_system = trigger_system

        # user-configuration values
        self.threshold_dbfs = int(threshold_dbfs)
        self.highpass_hz = int(highpass_hz)
        self.wave_export_len_s = float(wave_export_len_s)
        self.inter_recording_pause_s = float(inter_recording_pause_s)

        sampling_rate = 250000
        input_block_time = 0.05
        input_frames_per_block = int(sampling_rate * input_block_time)
        self.input_frames_per_block = input_frames_per_block
        blocks_per_sec = sampling_rate / input_frames_per_block

        self.silence_duration_blocks = float(
            silence_duration_s) / input_block_time
        self.max_tap_blocks = float(noise_duration_s) / input_block_time

        # set pyaudio config
        self.pa = pyaudio.PyAudio()
        self.pa_config = {
            "format": pyaudio.paInt16,
            "channels": 1,
            "rate": sampling_rate,
            "input": True,
            "frames_per_buffer": input_frames_per_block,
        }

        # compute higher values
        self.freq_bins_hz = np.arange(
            (input_frames_per_block / 2) + 1) / \
            (input_frames_per_block / float(self.pa_config["rate"]))

        self.window_function_dbfs_max = np.sum(input_frames_per_block) / 2.0

        blocks_per_sec = self.pa_config["rate"] / \
            self.pa_config["frames_per_buffer"]
        self.ring_buffer = RingBuffer(
            int(float(ring_buffer_len_s) * blocks_per_sec), dtype=np.str)

        # open input stream
        device_index = self.__find_input_device()
        self.stream = self.pa.open(
            input_device_index=device_index, **self.pa_config)

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
        for device_index in range(self.pa.get_device_count()):
            dev_info = self.pa.get_device_info_by_index(device_index)
            logger.debug(f"Device {device_index}: {dev_info['name']}")

            for keyword in ["mic", "input"]:
                if keyword in dev_info["name"].lower():
                    logger.info(
                        f"Found an input: device {device_index} - {dev_info['name']}")
                    return device_index

        logger.info("No preferred input found; using default input device.")

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
        file_path = self.data_path + self.current_start_time_str + ".wav"
        logger.info(f"file name: {file_path}")
        wave_file = wave.open(file_path, 'wb')

        wave_file.setnchannels(self.pa_config["channels"])
        wave_file.setsampwidth(
            self.pa.get_sample_size(self.pa_config["format"]))
        wave_file.setframerate(self.pa_config["rate"])
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
            if self.quiet_count > self.silence_duration_blocks and self.audio_recording:
                if time.time() > self.current_trigger_time + self.inter_recording_pause_s:
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
        execute a fft for a given signal and cuts the the frequencies below self.highpass_hz
        and return the resulting spectrum
        :param signal: givem signal to process the fft function
        :return:
        """
        data_int16 = np.frombuffer(signal, dtype=np.int16)
        spectrum = np.fft.rfft(data_int16)
        spectrum[self.freq_bins_hz < self.highpass_hz] = 0.000000001
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
            self.pa_config["rate"] / self.input_frames_per_block
        logger.debug(f"Peak freq hz: {peak_frequency_hz} dBFS: {peak_db}")
        return peak_db

    def __check_signal_for_threshold(self, peak_db) -> bool:
        return peak_db > self.threshold_dbfs

    def __is_time_for_audio_split(self) -> bool:
        return time.time() > self.current_trigger_time + self.wave_export_len_s


class VHF(AbstractSensor):
    def __init__(self,
                 trigger_system,
                 frequencies: [int],
                 frequency_range: int,
                 middle_frequency: int,
                 inactive_threshold: int,
                 threshold: float,
                 duration: float,
                 time_between_pings_in_sec: float,
                 db_user: str = "pi",
                 db_password: str = "natur",
                 db_database: str = "rteu",
                 **kwargs,
                 ):

        # instance variables
        self.trigger_system = trigger_system

        # user-configuration values
        self.frequencies = [int(f) for f in frequencies]
        self.frequency_range = int(frequency_range)
        self.middle_frequency = int(middle_frequency)
        self.inactive_threshold = int(inactive_threshold)
        # self.time_between_vhf_pings_in_sec = time_between_pings_in_sec
        self.threshold = float(threshold)
        self.duration = float(duration)

        # database connection
        self.db_user = str(db_user)
        self.db_password = str(db_password)
        self.db_database = str(db_database)

        # derived values
        self.observation_time_for_ping_in_sec = float(
            time_between_pings_in_sec) * 5 + 0.1

        # internal values
        self.active_frequencies = copy.deepcopy(self.frequencies)
        self.running = False
        self.threads = []
        self.vhf_recording = False
        self.trigger_events_since_last_status = 0
        self.present_and_active_bats = []

    def stop(self):
        self.running = False
        for t in self.threads:
            logger.debug(f"waiting for VHF thread {t._target}")
            t.join()
            self.threads.remove(t)
        logger.debug("VHF threads finished")

    def start(self):
        logging.info("Starting VHF sensor")
        self.running = True

        bat_scan_t = threading.Thread(target=self.__scan_for_active_bats)
        bat_scan_t.start()
        self.threads.append(bat_scan_t)

        frequency_scan_t = threading.Thread(
            target=self.__scan_frequency_inactivity)
        frequency_scan_t.start()
        self.threads.append(frequency_scan_t)

    def get_status(self):
        """
        delivers some nice information about the audio sensor since the last call
        :return:
        """
        present_and_inactive_bats = [
            x for x in self.frequencies if x not in self.active_frequencies]
        absent_bats = [x for x in self.frequencies if
                       x not in (present_and_inactive_bats or self.present_and_active_bats)]
        return_values = {"running": self.running,
                         "recording": self.vhf_recording,
                         "trigger events": self.trigger_events_since_last_status,
                         "bats present and inactive": present_and_inactive_bats,
                         "bats present and active": self.present_and_active_bats,
                         "bats absent": absent_bats,
                         "all observed frequencies": self.frequencies}
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
        for wanted_frequency in self.active_frequencies:
            if wanted_frequency - self.frequency_range < \
                    (frequency + self.middle_frequency) / 1000 < \
                    wanted_frequency + self.frequency_range:
                return True
        return False

    def __is_frequency_a_bat_frequency(self, frequency: int):
        """
        True if the given frequency is in the frequency range of any of the potential bat frequencies else False
        :param frequency: frequency to check
        :return: bool
        """
        for wanted_frequency in self.frequencies:
            if wanted_frequency - self.frequency_range < \
                    (frequency + self.middle_frequency) / 1000 \
                    < wanted_frequency + self.frequency_range:
                return True
        return False

    def __get_matching_bat_frequency(self, frequency: int):
        """
        :param frequency: given frequency of signal
        :return: the matching frequency out of self.config.frequencies
        """
        for wanted_frequency in self.frequencies:
            if wanted_frequency - self.frequency_range < \
                    (frequency + self.middle_frequency) / 1000 < \
                    wanted_frequency + self.frequency_range:
                return wanted_frequency

    def __scan_for_active_bats(self):
        """
        an always running thread for the continuous check of all frequencies for activity
        if activity is detected the thread starts the recording
        """
        last_vhf_ping = datetime.datetime.now()
        while True:
            try:
                current_round_check = False
                if not self.running:
                    break
                now = datetime.datetime.utcnow()
                start = time.time()
                query_results = self.__query_for_frequency_and_signal_strength(self.threshold,
                                                                               self.duration,
                                                                               now - datetime.timedelta(seconds=self.observation_time_for_ping_in_sec))
                logger.debug(
                    f"query check for active bats takes {time.time() - start}s")
                now = datetime.datetime.utcnow()
                self.present_and_active_bats = []
                for result in query_results:
                    frequency, signal_strength = result
                    real_frequency = (
                        frequency + self.middle_frequency) / 1000.0
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

    def __scan_frequency_inactivity(self):
        """
        an always running thread for continuous adding
        and removing frequencies from the currently active frequencies list
        """
        while True:
            try:
                if not self.running:
                    break
                now = datetime.datetime.utcnow()
                start = time.time()
                query_results = self.__query_for_frequency_and_signal_strength(self.threshold,
                                                                               self.duration,
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
                            and np.std(signals[frequency]) < self.inactive_threshold  \
                            and frequency in self.active_frequencies:
                        logger.info(f"remove frequency: {frequency}")
                        self.active_frequencies.remove(frequency)
                    elif np.std(signals[frequency]) > self.inactive_threshold \
                            and frequency not in self.active_frequencies:
                        if frequency not in self.active_frequencies:
                            logger.info(f"add frequency: {frequency}")
                            self.active_frequencies.append(
                                frequency)

                for frequency in self.frequencies:
                    if frequency not in signals.keys() and frequency not in self.active_frequencies:
                        logger.info(f"add frequency: {frequency}")
                        self.active_frequencies.append(frequency)
                time.sleep(10)
            except Exception as e:
                logger.warning(
                    f"Error in check_vhf_frequencies_for_inactivity: {e}")
