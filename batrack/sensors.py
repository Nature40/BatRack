import logging
import datetime
import time
import threading
import wave
import copy
import json
import os
from collections import defaultdict
from typing import List, Tuple, Dict

import numpy as np
import RPi.GPIO as GPIO
import pyaudio
import mysql.connector as mariadb
from numpy_ringbuffer import RingBuffer


logger = logging.getLogger(__name__)


class AbstractSensor(threading.Thread):
    def __init__(self,
                 use_trigger: bool,
                 trigger_callback: callable,
                 data_path: str = ".",
                 **kwargs,
                 ):
        super().__init__()

        self.use_trigger = bool(use_trigger)
        self.data_path = data_path

        self._trigger_callback = trigger_callback

        self._running = False
        self._trigger = False
        self._recording = False

        if len(kwargs) > 1:
            logger.debug(f"unused configuration parameters: {kwargs}")

    @property
    def recording(self) -> bool:
        """Return, wether the sensors values are recorded."""
        return self._recording

    @property
    def trigger(self):
        """Return trigger state based on this sensor."""
        return self._trigger

    def _set_trigger(self, trigger):
        logger.info(f"setting {self.__class__.__name__} trigger: {trigger}")
        self._trigger = trigger
        self._trigger_callback(trigger)

    def stop(self):
        """Stop and join the running threaded sensor.
        """
        self.stop_recording()
        self._running = False
        self.join()

    def start_recording(self):
        """Start recording of the sensor.

        The sensor instance requires to run.
        """
        logger.warning(
            f"{self.__class__.__name__}.start_recording() is not implemented.")

    def stop_recording(self):
        """Stop recording of the sensor.

        The sensor instance requires to run and a recording needs to be running.
        """
        logger.warning(
            f"{self.__class__.__name__}.stop_recording() is not implemented.")

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "recording": self._recording,
            "trigger": self._trigger,
        }


class CameraLightController(AbstractSensor):
    def __init__(self,
                 light_pin: int,
                 **kwargs):
        """Camera and light sensor, currently only supporting recording.

        Args:
            light_pin (int): GPIO pin to be used to controll the light.
        """
        super().__init__(**kwargs)

        # set the configuration light pin
        self.light_pin = int(light_pin)

        # initialize GPIO communication
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.light_pin, GPIO.OUT)
        GPIO.output(self.light_pin, GPIO.LOW)

        # initialize instance variables
        self.triggered_events_since_last_status = 0

    def run(self):
        self._running = True

        # camera software is running in a system process and does
        # not require any active computations here
        while self._running:
            logger.debug("sensor running")
            time.sleep(1)

    def stop(self):
        self._running = False

        GPIO.output(self.light_pin, GPIO.LOW)
        GPIO.cleanup()

        self.join()

    def start_recording(self):
        logger.info("Powering light on")
        GPIO.output(self.light_pin, GPIO.HIGH)

        logger.info("Starting camera recording")
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("1")

        self._recording = True

    def stop_recording(self):
        logger.info("Stopping camera recording")
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("0")

        logger.info("Powering light off")
        GPIO.output(self.light_pin, GPIO.LOW)

        self._recording = False


class Audio(AbstractSensor):
    def __init__(self,
                 threshold_dbfs: int,
                 highpass_hz: int,
                 wave_export_len_s: float,
                 quiet_threshold_s: float,
                 noise_threshold_s: float,
                 inter_recording_pause_s: float,
                 sampling_rate: int = 250000,
                 input_block_duration: float = 0.05,
                 **kwargs,
                 ):
        """Bat call audio sensor.

        Args:
            threshold_dbfs (int): Loudness threshold for a noisy block.
            highpass_hz (int): Frequency for highpass filter.
            wave_export_len_s (float): Maximum duration of an exported wave file.
            quiet_threshold_s (float): Duration of silence, after which the trigger is unset.
            noise_threshold_s (float): Duration of noise (bat calls), after which the trigger is set.
            inter_recording_pause_s (float): [description]
            sampling_rate (int, optional): Sampling rate of the microphone, defaults to 250000.
            input_block_duration (float, optional): Length of input blocks for analysis, defaults to 0.05.
        """
        super().__init__(**kwargs)

        # user-configuration values
        self.threshold_dbfs = int(threshold_dbfs)
        self.highpass_hz = int(highpass_hz)
        # TODO: this is not used currently
        self.inter_recording_pause_s = float(inter_recording_pause_s)

        self.sampling_rate = int(sampling_rate)
        self.input_block_duration = float(input_block_duration)
        self.input_frames_per_block = \
            int(self.sampling_rate * input_block_duration)

        self.wave_export_len = float(wave_export_len_s) * self.sampling_rate

        self.quiet_blocks_max = float(quiet_threshold_s) / input_block_duration
        self.noise_blocks_max = float(noise_threshold_s) / input_block_duration

        # set pyaudio config
        self.pa = pyaudio.PyAudio()

        self.__pings = 0

        self.__noise_blocks = 0
        self.__quiet_blocks = 0
        self.__wave = None

    def run(self):
        """
        observes the audio stream continuous if no trigger is used or process every frame for an trigger and
        store frames only in case of a trigger.
        Additionally it checks if the current audio dump is longer than the expected chunk and has to be splitted
        : param use_trigger: decides if the recording is continuous or triggered by the audio itself
        : return:
        """
        self._running = True

        # open input stream
        device_index = self.__find_input_device()
        stream = self.pa.open(
            input_device_index=device_index,
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sampling_rate,
            input=True,
            frames_per_buffer=self.input_frames_per_block,
        )

        while self._running:
            frame = stream.read(self.input_frames_per_block,
                                exception_on_overflow=False)

            self.__analyse_frame(frame)

            # if a wave file is opened, write the frame to this file
            if self.__wave:
                self.__wave_write(frame)

        # left while-loop, clean up
        if self.__wave:
            self.__wave_finalize()

        stream.stop_stream()
        stream.close()
        self.pa.terminate()

    def start_recording(self):
        self.__wave_initialize()
        self._recording = True

    def stop_recording(self):
        self.__wave_finalize()
        self._recording = False

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

    def __wave_initialize(self):
        if self.__wave:
            logger.warning("another wave is opened, not creating new file.")
            return

        start_time_str = datetime.datetime.now().strftime("%Y-%m-%dT%H_%M_%S")
        file_path = os.path.join(
            self.data_path, start_time_str + ".wav")

        logger.info(f"creating wav file '{file_path}'")
        self.__wave = wave.open(file_path, 'wb')
        self.__wave.setnchannels(1)
        self.__wave.setsampwidth(self.pa.get_sample_size(pyaudio.paInt16))
        self.__wave.setframerate(self.sampling_rate)

    def __wave_write(self, frame):
        remaining_length = int(self.wave_export_len -
                               self.__wave._nframeswritten)

        if len(frame) > remaining_length:
            logger.info(f"wave reached maximum, starting new file...")
            self.__wave_finalize()
            self.__wave_initialize()

        logger.debug(f"writing frame, len: {len(frame)}")
        self.__wave.writeframes(frame)

    def __wave_finalize(self):
        if not self.__wave:
            logger.warning("no wave is opened, skipping finalization request")
            return

        self.__wave.close()
        self.__wave = None

    def __analyse_frame(self, frame: str):
        """checks for the given frame if a trigger is present

        Args:
            frame (str): the recorded audio frame to be analysed
        """

        spectrum = self.__exec_fft(frame)
        peak_db = self.__get_peak_db(spectrum)

        # noisy block
        if peak_db > self.threshold_dbfs:
            self.__quiet_blocks = 0
            self.__noise_blocks += 1

        # quiet block
        else:
            # TODO: what does this? (somehow related to the click of the relay)
            if 1 <= self.__noise_blocks <= self.noise_blocks_max:
                logger.info(f"ping {self.__pings}")
                self.__pings += 1

            # set trigger and callback
            if 2 <= self.__pings and not self._trigger:
                self._set_trigger(True)

            # stop audio if thresbold of quiet blocks is met
            if self.__quiet_blocks > self.quiet_blocks_max and self._trigger:
                self._set_trigger(True)
                self.__pings = 0

            self.__noise_blocks = 0
            self.__quiet_blocks += 1

    def __exec_fft(self, signal) -> np.fft.rfft:
        """
        execute a fft for a given signal and cuts the the frequencies below self.highpass_hz
        and return the resulting spectrum
        :param signal: givem signal to process the fft function
        :return:
        """
        # do the fft
        data_int16 = np.frombuffer(signal, dtype=np.int16)
        spectrum = np.fft.rfft(data_int16)

        # compute target frequencies
        freq_bins_hz = np.arange(
            (self.input_frames_per_block / 2) + 1) / \
            (self.input_frames_per_block / float(self.sampling_rate))

        # apply the highpass
        spectrum[freq_bins_hz < self.highpass_hz] = 0.000000001

        return spectrum

    def __get_peak_db(self, spectrum: np.fft) -> int:
        """
        returns the maximum db of the given spectrum
        :param spectrum:
        :return:
        """

        window_function_dbfs_max = np.sum(self.input_frames_per_block) / 2.0
        dbfs_spectrum = 20 * np.log10(np.abs(spectrum) /
                                      max([window_function_dbfs_max, 1]))
        bin_peak_index = dbfs_spectrum.argmax()
        peak_db = dbfs_spectrum[bin_peak_index]
        peak_frequency_hz = bin_peak_index * \
            self.sampling_rate / self.input_frames_per_block
        logger.debug(f"Peak freq hz: {peak_frequency_hz} dBFS: {peak_db}")
        return peak_db


class VHF(AbstractSensor):
    def __init__(self,
                 freq_center_hz: int,
                 freq_bw_hz: int,
                 sig_freqs_mhz: List[float],
                 sig_threshold_dbm: float,
                 sig_duration_threshold_s: float,
                 sig_poll_interval_s: float,
                 freq_active_window_s: float,
                 freq_active_var: float,
                 freq_active_count: int,
                 untrigger_duration_s: float,
                 db_user: str = "pi",
                 db_password: str = "natur",
                 db_database: str = "rteu",
                 **kwargs,
                 ):
        """[summary]

        Args:
            freq_center_hz (int): center frequency of signal_detect, used to compute absolute frequencies from database entries 
            freq_bw_hz (int): bandwidth used by a sender, required to match received signals to defined frequencies 
            sig_freqs_mhz (List[float]): list of frequencies to monitor
            sig_threshold_dbm (float): power threshold for received signals
            sig_duration_threshold_s (float): duration threshold for received signals 
            sig_poll_interval_s (float): interval in which the database is polled for new signals
            freq_active_window_s (float): duration of window used for active / passive freq classification
            freq_active_var (float): threshold, after which a frequency is classified active
            freq_active_count (int): required number of signals in a frequencyy for classifaciton
            untrigger_duration_s (float): duration for which a trigger will stay active
            db_user (str, optional): local mariadb username. Defaults to "pi".
            db_password (str, optional): local mariadb password. Defaults to "natur".
            db_database (str, optional): local mariadb database name. Defaults to "rteu".

        Raises:
            ValueError: format of an argument is not valid.
        """
        super().__init__(**kwargs)

        # base system values
        self.freq_center_hz = int(freq_center_hz)
        self.freq_bw_hz = int(freq_bw_hz)

        # signal-specific configuration and thresholds
        if isinstance(sig_freqs_mhz, list):
            sig_freqs_mhz = [float(f) for f in sig_freqs_mhz]
        elif isinstance(sig_freqs_mhz, str):
            sig_freqs_mhz = [float(f) for f in json.loads(sig_freqs_mhz)]
        else:
            raise ValueError(
                f"invalid format for frequencies, {type(sig_freqs_mhz)}:'{sig_freqs_mhz}'")

        # freqs_bins to contain old signal values for variance calc
        self._freqs_bins = {}
        for freq_mhz in sig_freqs_mhz:
            freq_rel = int(freq_mhz * 1000 * 1000) - self.freq_center_hz
            lower = freq_rel - (self.freq_bw_hz / 2)
            upper = freq_rel + (self.freq_bw_hz / 2)

            self._freqs_bins[freq_mhz] = (lower, upper, [])

        self.sig_threshold_dbm = float(sig_threshold_dbm)
        # TODO: Signal duration threshold is not yet used
        self.sig_duration_threshold_s = float(sig_duration_threshold_s)
        self.sig_poll_interval_s = float(sig_poll_interval_s)

        self.freq_active_window_s = float(freq_active_window_s)
        self.freq_active_var = float(freq_active_var)
        self.freq_active_count = int(freq_active_count)

        self.untrigger_duration_s = float(untrigger_duration_s)

        # database connection
        self.db_user = str(db_user)
        self.db_password = str(db_password)
        self.db_database = str(db_database)

        # create db connection to validate access
        self.__db = mariadb.connect(user=self.db_user,
                                    password=self.db_password,
                                    database=self.db_database,
                                    autocommit=True,
                                    )

    def run(self):
        self._running = True

        # setup db cursor and get initial signal retrieval pointer
        cursor = self.__db.cursor()
        cursor.execute("SET time_zone='+00:00';")
        cursor.execute(
            "SELECT id, timestamp FROM signals ORDER BY id DESC LIMIT 1;")
        db_id, db_ts = cursor.fetchone()
        logger.info(
            f"Reading signals of database, starting with id:{db_id}, datetime: {db_ts}")

        # helper method to retrieve the signal list

        def get_freqs_list(freq_rel: int) -> Tuple[float, list]:
            for mhz, (lower, upper, sigs) in self._freqs_bins.items():
                if freq_rel > lower and freq_rel < upper:
                    return (mhz, sigs)

            return (None, None)

        untrigger_ts = 0

        # query to get the latest signal entries
        query = "SELECT id, timestamp, signal_freq, max_signal " + \
            "FROM signals WHERE " + \
            "id > %s;"

        while self._running:
            # get and iterate latest detected signals
            cursor.execute(query, (db_id, ))

            for db_id, db_ts, freq_rel, sig_strength in cursor.fetchall():
                frequency_mhz, sigs = get_freqs_list(freq_rel)

                if not frequency_mhz:
                    sig_freq_mhz = (
                        freq_rel + self.freq_center_hz) / 1000.0 / 1000.0
                    logger.debug(
                        f"signal {sig_freq_mhz:.3f} MHz: not in sig_freqs_mhz list, discarding")
                    continue

                # append current signal to the signal list of this freq
                sigs.append((db_ts, sig_strength))

                # discard signals below threshold
                if sig_strength < self.sig_threshold_dbm:
                    logger.info(
                        f"signal {frequency_mhz:.3f} MHz, {sig_strength} dBm: too weak, discarding")
                    continue

                # cleanup current signal list (discard older signals)
                sig_start = db_ts - \
                    datetime.timedelta(seconds=self.freq_active_window_s)
                sigs[:] = [sig for sig in sigs if sig[0] > sig_start]

                # check if count threshold is met
                count = len(sigs)
                if count < self.freq_active_count:
                    logger.info(
                        f"signal {frequency_mhz:.3f} MHz, {sig_strength} dBm: signals count low ({count}), discarding")
                    continue

                # check if freq can be considered active
                var = np.std([sig[1] for sig in sigs])
                if var < self.freq_active_var:
                    logger.info(
                        f"signal {frequency_mhz:.3f} MHz, {sig_strength} dBm: frequency variance low ({var}), discarding")
                    continue

                # set untrigger time if all criterions are met
                logger.info(
                    f"signal: {frequency_mhz:.3f} MHz, {sig_strength} dBm: met all conditions (sig_count: {count}, sig_var: {var}:.3f)")

                # TODO: set this from db_ts, instead of local time
                # this could lead to decreasing of untrigger_ts, which could be avoided by calling max(untrigger_ts_old, ..._new)
                untrigger_ts = time.time() + self.untrigger_duration_s

            # if untrigger time is over
            if untrigger_ts < time.time():
                if self._trigger:
                    self._set_trigger(False)

            # untrigger time is not over
            else:
                if not self._trigger:
                    self._set_trigger(True)

            # higher sleep time decreases load, increases jitter / delay for new signals
            time.sleep(self.sig_poll_interval_s)

        cursor.close()
        self.__db.close()
