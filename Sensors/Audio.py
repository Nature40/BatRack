import numpy as np
import pyaudio
import datetime
from numpy_ringbuffer import RingBuffer
import time
import wave
import threading
import Helper
from Sensors.Sensor import  Sensor


class Audio(Sensor):
    def __init__(self,
                 data_folder,
                 threshold_dbfs,
                 highpass_frequency,
                 ring_buffer_length_in_sec,
                 audio_split,
                 min_seconds_for_audio_recording,
                 debug_on,
                 trigger_system):
        self.data_folder = data_folder
        self.threshold_dbfs = threshold_dbfs
        self.audio_split = audio_split
        self.trigger_system = trigger_system
        self.min_seconds_for_audio_recording = min_seconds_for_audio_recording

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
        self.blocks_per_sec = self.sampling_rate / self.input_frames_per_block
        self.debug_on = debug_on

        self.filter_min_hz = highpass_frequency
        self.freq_bins_hz = np.arange((self.input_frames_per_block / 2) + 1) / (
                    self.input_frames_per_block / float(self.sampling_rate))
        self.window_function_dbfs_max = np.sum(self.input_frames_per_block) / 2.0

        self.ring_buffer_length_in_sec = ring_buffer_length_in_sec
        self.ring_buffer = RingBuffer(int(self.ring_buffer_length_in_sec * self.blocks_per_sec), dtype=np.str)

        self.stream = self.__open_mic_stream()

        self.current_start_time_str: str = ""
        self.current_start_time: int = 0
        self.frames = []
        self.recording_thread: threading = None
        self.recording_thread_stopped = False
        self.noisy_count = 0
        self.quiet_count = 0
        self.pings = 0
        self.audio_recording = False
        self.trigger_events_since_last_status = 0
        self.use_trigger = False

    def start(self, use_trigger: bool = False):
        """
        start audio recording if the microphone should be used
        :param use_trigger:
        :return:
        """
        Helper.print_message("Start audio sensor")
        self.__start_new_file()
        self.__record(use_trigger)

    def stop(self):
        """
        stop audio recording if the microphone should be used and start writing the audio to filesystem
        :return:
        """
        self.__stop_record()

    def clean_up(self):
        """
        stops the stream and terminates PyAudio. Additional it stops the recording first
        :return:
        """
        self.__stop_record()
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()

    def get_status(self) -> dict:
        """
        delivers some nice information about the audio sensor since the last call
        :return:
        """
        return_values = {"trigger events": self.trigger_events_since_last_status, "use trigger": self.use_trigger}
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
            Helper.print_message("Device {}: {}".format(i, dev_info["name"]), True)

            for keyword in ["mic", "input"]:
                if keyword in dev_info["name"].lower():
                    Helper.print_message("Found an input: device {} - {}".format(i, dev_info["name"]), True)
                    device_index = i
                    return device_index

        if device_index is None:
            Helper.print_message("No preferred input found; using default input device.", False)

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
        self.current_start_time = time.time()
        self.frames = []

    def __save(self):
        """
        store the last recorded audio to the filesystem and clears the list of frames
        :return:
        """
        Helper.print_message("len of frames: {}".format(len(self.frames)))
        Helper.print_message("file name: {}{}.wav".format(self.data_folder, self.current_start_time_str))
        wave_file = wave.open(self.data_folder + self.current_start_time_str + ".wav", 'wb')
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
                Helper.print_message("doing audio split")
                self.__save()
            if use_trigger:
                frame = self.__read_frame()
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
                Helper.print_message("ping")
            if self.pings >= 2 and not self.audio_recording:
                Helper.print_message("audio_recording started")
                self.trigger_events_since_last_status += 1
                self.audio_recording = True
                self.trigger_system.start_sequence_audio()
                self.current_start_time = time.time()
            if self.quiet_count > self.silence_time and self.audio_recording:
                if time.time() > self.current_start_time + self.min_seconds_for_audio_recording:
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
        self.recording_thread = threading.Thread(target=self.__observe_and_process_audio_stream, args=(use_trigger, ))
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
        dbfs_spectrum = 20 * np.log10(np.abs(spectrum) / max([self.window_function_dbfs_max, 1]))
        bin_peak_index = dbfs_spectrum.argmax()
        peak_db = dbfs_spectrum[bin_peak_index]
        if self.debug_on:
            peak_frequency_hz = bin_peak_index * self.sampling_rate / self.input_frames_per_block
            Helper.print_message("DEBUG: Peak freq hz: " + str(peak_frequency_hz) + " dBFS: " + str(peak_db), True)
        return peak_db

    def __check_signal_for_threshold(self, peak_db) -> bool:
        return peak_db > self.threshold_dbfs

    def __is_time_for_audio_split(self) -> bool:
        return time.time() > self.current_start_time + self.audio_split
