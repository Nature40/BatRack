import datetime
import json
import logging
import os
import threading
import time
import wave
import platform
from distutils.util import strtobool
from typing import Callable, Dict, List, Optional, Tuple, Union

import gpiozero
import cbor2 as cbor
import paho.mqtt.client as mqtt
import numpy as np
import pyaudio

from radiotracking import MatchedSignal
from radiotracking.consume import uncborify

logger = logging.getLogger(__name__)


class AbstractAnalysisUnit(threading.Thread):
    def __init__(
        self,
        use_trigger: Union[str, bool],
        trigger_callback: Callable,
        data_path: str = ".",
        **kwargs,
    ):
        super().__init__()

        self.use_trigger: bool = strtobool(use_trigger) if isinstance(use_trigger, str) else bool(use_trigger)
        self.data_path: str = str(data_path)

        self._trigger_callback: Callable = trigger_callback

        self._running: bool = False
        self._trigger: bool = False
        self._recording: bool = False

        if len(kwargs) > 1:
            logger.debug(f"unused configuration parameters: {kwargs}")

    @property
    def recording(self) -> bool:
        """Return, wether the sensors values are recorded."""
        return self._recording

    @property
    def trigger(self) -> bool:
        """Return trigger state based on this sensor."""
        return self._trigger

    def _set_trigger(self, trigger, message):
        logger.info(f"setting {self.__class__.__name__} trigger {trigger}: {message}")
        self._trigger = trigger
        self._trigger_callback(trigger, message)

    def stop(self):
        """Stop and join the running threaded sensor."""
        self.stop_recording()
        self._running = False
        self.join()

    def start_recording(self):
        """Start recording of the sensor.

        The sensor instance requires to run.
        """
        logger.warning(f"{self.__class__.__name__}.start_recording() is not implemented.")

    def stop_recording(self):
        """Stop recording of the sensor.

        The sensor instance requires to run and a recording needs to be
        running.
        """
        logger.warning(f"{self.__class__.__name__}.stop_recording() is not implemented.")

    def get_status(self) -> Dict:
        return {
            "running": self._running,
            "alive": self.is_alive(),
            "recording": self._recording,
            "use_trigger": self.use_trigger,
            "trigger": self._trigger,
        }


class CameraAnalysisUnit(AbstractAnalysisUnit):
    def __init__(self, light_pin: int, **kwargs):
        """Camera and light sensor, currently only supporting recording.

        Args:
            light_pin (int): GPIO pin to be used to controll the light.
        """
        super().__init__(**kwargs)

        # initialize GPIO communication
        self.light: gpiozero.LED = gpiozero.LED(light_pin, active_high=False)

    def run(self):
        self._running = True

        # camera software is running in a system process and does
        # not require any active computations here
        while self._running:
            logger.debug("sensor running")
            time.sleep(1)

    def start_recording(self):
        logger.info("Powering light on")
        self.light.on()

        logger.info("Starting camera recording")
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("1")

        self._recording = True

    def stop_recording(self):
        logger.info("Stopping camera recording")
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("0")

        logger.info("Powering light off")
        self.light.off()

        self._recording = False


class AudioAnalysisUnit(AbstractAnalysisUnit):
    def __init__(
        self,
        threshold_dbfs: int,
        highpass_hz: int,
        wave_export_len_s: float,
        quiet_threshold_s: float,
        noise_threshold_s: float,
        sampling_rate: int = 250000,
        input_block_duration: float = 0.05,
        **kwargs,
    ):
        """Bat call audio sensor.

        Args:
            threshold_dbfs (int): Loudness threshold for a noisy block.
            highpass_hz (int): Frequency for highpass filter.
            wave_export_len_s (float): Maximum duration of an exported wave.
            quiet_threshold_s (float): Silence duration for trigger unset.
            noise_threshold_s (float): Noise duration, to set trigger.
            sampling_rate (int, optional): Sampling rate of the microphone.
            input_block_duration (float, optional): Length of input blocks.
        """
        super().__init__(**kwargs)

        # user-configuration values
        self.threshold_dbfs: int = int(threshold_dbfs)
        self.highpass_hz: int = int(highpass_hz)

        self.sampling_rate: int = int(sampling_rate)
        self.input_block_duration: float = float(input_block_duration)
        self.input_frames_per_block: int = int(self.sampling_rate * input_block_duration)

        self.wave_export_len: float = float(wave_export_len_s) * self.sampling_rate

        self.quiet_blocks_max: float = float(quiet_threshold_s) / input_block_duration
        self.noise_blocks_max: float = float(noise_threshold_s) / input_block_duration

        # set pyaudio config
        self.pa: pyaudio.PyAudio = pyaudio.PyAudio()

        self.__pings: int = 0

        self.__noise_blocks: int = 0
        self.__quiet_blocks: int = 0
        self.__wave = None

    def run(self):
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
            frame = stream.read(self.input_frames_per_block, exception_on_overflow=False)

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
        # TODO: isn't it enough to set self._reconging = False? In run()
        # __wave_finalize() is also called.
        self.__wave_finalize()
        self._recording = False

    def __find_input_device(self) -> Optional[int]:
        """
        searches for a microphone and returns the device number
        :return: the device id
        """
        for device_index in range(self.pa.get_device_count()):
            dev_info = self.pa.get_device_info_by_index(device_index)
            logger.debug(f"Device {device_index}: {dev_info['name']}")

            for keyword in ["mic", "input"]:
                if keyword in dev_info["name"].lower():
                    logger.info(f"Found an input: device {device_index} - {dev_info['name']}")
                    return device_index

        logger.info("No preferred input found; using default input device.")
        return None

    def __wave_initialize(self):
        if self.__wave:
            logger.warning("another wave is opened, not creating new file.")
            return

        start_time_str = datetime.datetime.now().strftime("%Y-%m-%dT%H_%M_%S")
        file_path = os.path.join(self.data_path, start_time_str + ".wav")

        logger.info(f"creating wav file '{file_path}'")
        self.__wave = wave.open(file_path, "wb")
        self.__wave.setnchannels(1)
        self.__wave.setsampwidth(self.pa.get_sample_size(pyaudio.paInt16))
        self.__wave.setframerate(self.sampling_rate)

    def __wave_write(self, frame):
        remaining_length = int(self.wave_export_len - self.__wave._nframeswritten)

        if len(frame) > remaining_length:
            logger.info("wave reached maximum, starting new file...")
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
            # ping detection; a ping has to be a noisy sequence which is not
            # longer than self.noise_blocks_max
            if 1 <= self.__noise_blocks <= self.noise_blocks_max:
                logger.info(f"ping {self.__pings}")
                self.__pings += 1

            # set trigger and callback
            # it's the second ping because of the *click* of the relays which
            # is the first ping every time
            # in the moment we done have a relay anymore we can delete the
            # lower boundary
            if 2 <= self.__pings and not self._trigger:
                self._set_trigger(True, f"audio, {self.__pings} pings")

            # stop audio if thresbold of quiet blocks is met
            if self.__quiet_blocks > self.quiet_blocks_max and self._trigger:
                self._set_trigger(False, f"audio, {self.__quiet_blocks} quiet blocks")
                self.__pings = 0

            self.__noise_blocks = 0
            self.__quiet_blocks += 1

    def __exec_fft(self, signal) -> np.fft.rfft:
        """execute a fft on given samples and apply highpass filter

        Args:
            signal ([type]): the input samples

        Returns:
            np.fft.rfft: highpass-filtered spectrum
        """
        # do the fft
        data_int16 = np.frombuffer(signal, dtype=np.int16)
        spectrum = np.fft.rfft(data_int16)

        # compute target frequencies
        freq_bins_hz = np.arange((self.input_frames_per_block / 2) + 1) / (self.input_frames_per_block / float(self.sampling_rate))

        # apply the highpass
        spectrum[freq_bins_hz < self.highpass_hz] = 0.000000001

        return spectrum

    def __get_peak_db(self, spectrum: np.fft) -> float:
        """extract the maximal volume of a given spectrum

        Args:
            spectrum (np.fft): spectrum to analyze

        Returns:
            float: the retrieved maximum
        """

        window_function_dbfs_max = np.sum(self.input_frames_per_block) / 2.0
        dbfs_spectrum = 20 * np.log10(np.abs(spectrum) / max([window_function_dbfs_max, 1]))
        bin_peak_index = dbfs_spectrum.argmax()
        peak_db = dbfs_spectrum[bin_peak_index]
        peak_frequency_hz = bin_peak_index * self.sampling_rate / self.input_frames_per_block
        logger.debug(f"Peak freq hz: {peak_frequency_hz} dBFS: {peak_db}")
        return peak_db


class VHFAnalysisUnit(AbstractAnalysisUnit):
    def __init__(
        self,
        freq_bw_hz: int,
        sig_freqs_mhz: List[float],
        sig_threshold_dbw: float,
        sig_duration_threshold_s: float,
        freq_active_window_s: float,
        freq_active_var: float,
        freq_active_count: int,
        untrigger_duration_s: float,
        mqtt_host: str = "localhost",
        mqtt_port: int = 1883,
        mqtt_keepalive: int = 60,
        **kwargs,
    ):
        """[summary]

        Args:
            freq_bw_hz (int): bandwidth used by a sender, required to match received signals to defined frequencies
            sig_freqs_mhz (List[float]): list of frequencies to monitor
            sig_threshold_dbw (float): power threshold for received signals
            sig_duration_threshold_s (float): duration threshold for received signals
            freq_active_window_s (float): duration of window used for active / passive freq classification
            freq_active_var (float): threshold, after which a frequency is classified active
            freq_active_count (int): required number of signals in a frequencyy for classifaciton
            untrigger_duration_s (float): duration for which a trigger will stay active

        Raises:
            ValueError: format of an argument is not valid.
        """
        super().__init__(**kwargs)

        # base system values
        self.freq_bw_hz: int = int(freq_bw_hz)

        # signal-specific configuration and thresholds
        if isinstance(sig_freqs_mhz, list):
            sig_freqs_mhz = [float(f) for f in sig_freqs_mhz]
        elif isinstance(sig_freqs_mhz, str):
            sig_freqs_mhz = [float(f) for f in json.loads(sig_freqs_mhz)]
        else:
            raise ValueError(f"invalid format for frequencies, {type(sig_freqs_mhz)}:'{sig_freqs_mhz}'")

        # freqs_bins to contain old signal values for variance calc
        self._freqs_bins: Dict[float, Tuple[float, float, List[Tuple[datetime.datetime, float]]]] = {}
        for freq_mhz in sig_freqs_mhz:
            freq_rel = int(freq_mhz * 1000 * 1000)
            lower = freq_rel - (self.freq_bw_hz / 2)
            upper = freq_rel + (self.freq_bw_hz / 2)

            self._freqs_bins[freq_mhz] = (lower, upper, [])

        self.sig_threshold_dbw = float(sig_threshold_dbw)
        # TODO: Signal duration threshold is not yet used
        self.sig_duration_threshold_s = float(sig_duration_threshold_s)

        self.freq_active_window_s = float(freq_active_window_s)
        self.freq_active_var = float(freq_active_var)
        self.freq_active_count = int(freq_active_count)

        self.untrigger_duration_s = float(untrigger_duration_s)

        # create client object and set callback methods
        self.mqtt_host = str(mqtt_host)
        self.mqtt_port = int(mqtt_port)
        self.mqtt_keepalive = int(mqtt_keepalive)
        self.mqttc = mqtt.Client(client_id=f"{platform.node()}-BatRack", clean_session=False, userdata=self)

        self.untrigger_ts = time.time()

    def start_recording(self):
        # the vhf sensor is recording continuously
        pass

    def stop_recording(self):
        # the vhf sensor is recording continuously
        pass

    @staticmethod
    def on_matched_cbor(client: mqtt.Client, self, message):
        # extract payload and meta data
        matched_list = cbor.loads(message.payload, tag_hook=uncborify)
        station, _, _, _ = message.topic.split("/")

        msig = MatchedSignal(["0"], *matched_list)
        logging.debug(f"Received {msig}")

        # helper method to retrieve the signal list
        def get_freqs_list(freq: int) -> Tuple[Optional[float], List[Tuple[datetime.datetime, float]]]:
            for mhz, (lower, upper, sigs) in self._freqs_bins.items():
                if freq > lower and freq < upper:
                    return (mhz, sigs)

            return (None, [])

        previous_absent: bool = False
        frequency_mhz, sigs = get_freqs_list(msig.frequency)

        if not frequency_mhz:
            logger.debug(f"signal {msig.frequency/1000.0/1000.0:.3f} MHz: not in sig_freqs_mhz list, discarding")
            return

        # append current signal to the signal list of this freq
        sigs.append((msig.ts, msig._avgs[0]))

        # discard signals below threshold
        if msig._avgs[0] < self.sig_threshold_dbw:
            logger.debug(f"signal {frequency_mhz:.3f} MHz, {msig._avgs[0]:.3f} dBW: too weak, discarding")
            return

        # cleanup current signal list (discard older signals)
        sig_start = msig.ts - datetime.timedelta(seconds=self.freq_active_window_s)
        sigs[:] = [sig for sig in sigs if sig[0] > sig_start]

        # check if bats was absent before
        count = len(sigs)
        if count < self.freq_active_count:
            previous_absent = True
            logger.debug(f"signal {frequency_mhz:.3f} MHz, {msig._avgs[0]:.3f} dBW: one of the first signals => match")

        # check if bat is active
        if not previous_absent:
            var = np.std([sig[1] for sig in sigs])
            if var < self.freq_active_var:
                logger.debug(f"signal {frequency_mhz:.3f} MHz, {msig._avgs[0]:.3f} dBW: frequency variance low ({var}), discarding")
                return
            else:
                logger.debug(f"signal: {frequency_mhz:.3f} MHz, {msig._avgs[0]:.3f} dBW: met all conditions (sig_count: {count}, sig_var: {var:.3f})")

        # set untrigger time if all criterions are met
        # TODO: set this from db_ts, instead of local time
        # this could lead to decreasing of untrigger_ts, which could be avoided by calling max(untrigger_ts_old, ..._new)
        # if this is correct the 'sigs[:] = [sig for sig in sigs if sig[0] > sig_start]' statement should also be incorrect in some cases
        self.untrigger_ts = time.time() + self.untrigger_duration_s
        self._set_trigger(True, f"vhf, {frequency_mhz:.3f} MHz, {msig._avgs[0]:.3f} dBW, {count} sigs")

    @staticmethod
    def on_connect(mqttc: mqtt.Client, self, flags, rc):
        logging.info(f"MQTT connection established ({rc})")

        # subscribe to match signal cbor messages
        topic_matched_cbor = "+/radiotracking/matched/cbor"
        mqttc.subscribe(topic_matched_cbor)
        mqttc.message_callback_add(topic_matched_cbor, self.on_matched_cbor)
        logging.info(f"Subscribed to {topic_matched_cbor}")

    def run(self):
        self._running = True
        self.mqttc.on_connect = self.on_connect

        ret = self.mqttc.connect(self.mqtt_host, self.mqtt_port, self.mqtt_keepalive)
        if ret != mqtt.MQTT_ERR_SUCCESS:
            logging.critical(f"MQTT connetion failed: {ret}")

        while self._running:
            self.mqttc.loop(0.1)
            if self.untrigger_ts < time.time():
                if self._trigger:
                    self._set_trigger(False, "vhf, timeout")

        self.mqttc.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    vhf = VHFAnalysisUnit(8000, [150.611], -80, 0.01, 60, 2, 10, 10, "localhost", 1883, 60, use_trigger=True, trigger_callback=lambda trg, msg: logger.debug(f"Trigger: {trg}, {msg}"))
    vhf.run()
