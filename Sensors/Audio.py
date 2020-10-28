import numpy as np
import pyaudio
import BatRack
import datetime

class Audio:
    def __init__(self, highpass_frequency, debug_on):
        self.highpass_frequency = highpass_frequency

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
        self.over_sensitive = 15.0 / self.input_block_time
        # if we get this many quiet blocks in a row, decrease the threshold
        self.under_sensitive = 120.0 / self.input_block_time
        self.blocks_per_sec = self.sampling_rate / self.input_frames_per_block
        self.debug_on = debug_on

        self.filter_min_hz = self.highpass_frequency
        self.freq_bins_hz = np.arange((self.input_frames_per_block / 2) + 1) / \
                            (self.input_frames_per_block / float(self.sampling_rate))
        self.window_function_dbfs_max = np.sum(self.input_frames_per_block) / 2.0

        self.stream = self.open_mic_stream()


    def read(self):
        return self.stream.read(self.input_frames_per_block, exception_on_overflow=False)

    def clean_up(self):
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()

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
            BatRack.print_message("DEBUG: Peak freq hz: " + str(peak_frequency_hz) + "   dBFS: " + str(peak_db), True)
        return peak_db


    def stop(self):
        '''closes the audio stream'''
        self.stream.close()


    def find_input_device(self):
        '''searches for a microphone and returns the device number'''
        device_index = None
        for i in range(self.pa.get_device_count()):
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

        stream = self.pa.open(format=self.format,
                              channels=self.channels,
                              rate=self.sampling_rate,
                              input=True,
                              input_device_index=device_index,
                              frames_per_buffer=self.input_frames_per_block)

        return stream

    def save_audio(self, frames):
        '''store the last recorded audio to the filesystem'''
        wavefile = wave.open(self.current_start_time + ".wav", 'wb')
        wavefile.setnchannels(self.channels)
        wavefile.setsampwidth(self.pa.get_sample_size(self.format))
        wavefile.setframerate(self.sampling_rate)
        wavefile.writeframes(b''.join(frames))
        wavefile.close()

    def __startAudio(self):
        '''start audio recording if the microphone should be used'''
        self.current_start_time = datetime.datetime.now().strftime("%Y-%m-%dT%H_%M_%S")
        self.frames = []

    def __stopAudio(self, frames):
        '''stop audio recording if the microphone should be used and start writing the audio to filesystem'''
        self.print_message("len of frames: {}".format(len(frames)))
        self.print_message("file name: {}.wav".format(self.current_start_time))
        self.save_audio(frames)