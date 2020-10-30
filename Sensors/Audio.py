import numpy as np
import pyaudio
import datetime
from numpy_ringbuffer import RingBuffer
import time
import wave
import threading
import Helper


class Audio:
    def __init__(self,
                 threshold_dbfs,
                 highpass_frequency,
                 ring_buffer_length_in_sec,
                 audio_split,
                 min_seconds_for_audio_recording,
                 debug_on,
                 trigger_system):
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
        self.last_ping: int = 0
        self.frames = []
        self.recording_thread: threading = None
        self.continuous_thread_stopped = False
        self.noisy_count = 0
        self.quiet_count = 0
        self.pings = 0
        self.audio_recording = False

    def get_max_tap_blocks(self):
        return self.max_tap_blocks

    def __read(self):
        return self.stream.read(self.input_frames_per_block, exception_on_overflow=False)

    def __read_continuous(self, use_trigger: bool):
        while True:
            if self.continuous_thread_stopped:
                self.__save()
                return
            if self.__is_time_for_audio_split():
                Helper.print_message("doing audio split")
                self.__save()
            if use_trigger:
                frame = self.__read()
                if self.__check_trigger_state(frame):
                    self.frames.append(frame)
            else:
                self.frames.append(self.__read())

    def __check_trigger_state(self, frame):
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
                self.last_ping = time.time()
                Helper.print_message("ping")
            if self.pings >= 2 and not self.audio_recording:
                Helper.print_message("audio_recording started")
                self.audio_recording = True
                self.trigger_system.start_sequence_audio()
                self.current_start_time = time.time()
            if self.quiet_count > self.silence_time:
                if time.time() > self.current_start_time + self.min_seconds_for_audio_recording:
                    self.pings = 0
                    self.audio_recording = False
                    self.trigger_system.stop_sequence_audio()
            self.noisy_count = 0
            self.quiet_count += 1
            return self.audio_recording

    def __record(self, triggered):
        self.recording_thread = threading.Thread(target=self.__read_continuous, args=(triggered))
        self.recording_thread.start()

    def __stop_record(self):
        self.continuous_thread_stopped = True
        if self.recording_thread is not None:
            self.recording_thread.terminate()

    def clean_up(self):
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()

    def __exec_fft(self, signal):
        """
        execute a fft for a given signal and cuts the the frequencies below self.filter_min_hz
        and return the resulting spectrum
        """
        data_int16 = np.frombuffer(signal, dtype=np.int16)
        spectrum = np.fft.rfft(data_int16)
        spectrum[self.freq_bins_hz < self.filter_min_hz] = 0.000000001
        return spectrum

    def __get_peak_db(self, spectrum: np.fft):
        """returns the maximum db of the given spectrum"""
        dbfs_spectrum = 20 * np.log10(np.abs(spectrum) / max([self.window_function_dbfs_max, 1]))
        bin_peak_index = dbfs_spectrum.argmax()
        peak_db = dbfs_spectrum[bin_peak_index]
        if self.debug_on:
            peak_frequency_hz = bin_peak_index * self.sampling_rate / self.input_frames_per_block
            Helper.print_message("DEBUG: Peak freq hz: " + str(peak_frequency_hz) + " dBFS: " + str(peak_db), True)
        return peak_db

    def stop_stream(self):
        """closes the audio stream"""
        self.stream.close()

    def __find_input_device(self):
        """searches for a microphone and returns the device number"""
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

        return device_index

    def __open_mic_stream(self):
        """open a PyAudio stream for the found device number and return the stream"""
        device_index = self.__find_input_device()

        stream = self.pa.open(format=self.format,
                              channels=self.channels,
                              rate=self.sampling_rate,
                              input=True,
                              input_device_index=device_index,
                              frames_per_buffer=self.input_frames_per_block)

        return stream

    def __start_new_file(self):
        self.current_start_time_str = datetime.datetime.now().strftime("%Y-%m-%dT%H_%M_%S")
        self.current_start_time = time.time()
        self.frames = []

    def __save(self):
        """store the last recorded audio to the filesystem and clears the list of frames"""
        Helper.print_message("len of frames: {}".format(len(self.frames)))
        Helper.print_message("file name: {}.wav".format(self.current_start_time_str))
        wave_file = wave.open(self.current_start_time_str + ".wav", 'wb')
        wave_file.setnchannels(self.channels)
        wave_file.setsampwidth(self.pa.get_sample_size(self.format))
        wave_file.setframerate(self.sampling_rate)
        wave_file.writeframes(b''.join(self.frames))
        wave_file.close()
        self.__start_new_file()

    def start(self, use_trigger: bool = False):
        """start audio recording if the microphone should be used"""
        self.__start_new_file()
        self.__record(use_trigger)

    def stop(self):
        """stop audio recording if the microphone should be used and start writing the audio to filesystem"""
        self.__stop_record()

    def __check_signal_for_threshold(self, peak_db):
        return peak_db > self.threshold_dbfs

    def __is_time_for_audio_split(self):
        return time.time() > self.current_start_time + self.audio_split
