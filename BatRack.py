import signal
import sys
import time
import threading
from Sensors.Audio import Audio
from Sensors.VHF import VHF
from Sensors.CameraLightController import CameraLightController
from Config import Config
from TriggerSystem import TriggerSystem
import Helper


class BatRack(object):
    def __init__(self, db_user: str, db_password: str, db_database: str, config_file_name: str,
                 ring_buffer_length_in_sec: int = 3):

        self.debug_on = False
        signal.signal(signal.SIGINT, self.signal_handler)

        self.db_user = db_user
        self.db_password = db_password
        self.db_database = db_database
        self.config = Config(config_file_name)

        self.use_camera = self.config.get_bool_from_config("use_camera")
        self.use_microphone = self.config.get_bool_from_config("use_microphone")
        self.use_audio_trigger = self.config.get_bool_from_config("use_audio_trigger")
        self.use_vhf_trigger = self.config.get_bool_from_config("use_vhf_trigger")
        self.run_continuous = self.config.get_bool_from_config("run_continuous")

        time.sleep(self.config.get_int_from_config("waiting_time_after_start"))

        self.camera_light_controller = None
        self.audio = None
        self.vhf = None

        if self.use_camera:
            self.camera_light_controller = CameraLightController(self.config.get_int_from_config("led_pin"))
        if self.use_microphone:
            self.audio = Audio(self.config.get_int_from_config("threshold_dbfs"),
                               self.config.get_int_from_config("highpass_frequency"),
                               ring_buffer_length_in_sec,
                               self.config.get_int_from_config("audio_split"),
                               self.config.get_int_from_config("min_seconds_for_audio_recording"),
                               self.debug_on,
                               self.trigger_system)
        if self.run_continuous:
            if self.use_camera:
                self.camera_light_controller.start()
            if self.use_microphone:
                self.audio.start(use_trigger=False)
        else:
            if self.use_vhf_trigger:
                self.vhf = VHF(db_user,
                               db_password,
                               db_database,
                               self.config.get_list_from_config("vhf_frequencies"),
                               self.config.get_int_from_config("frequency_range_for_vhf_frequency"),
                               self.config.get_int_from_config("vhf_middle_frequency"),
                               self.config.get_int_from_config("vhf_inactive_threshold"),
                               self.config.get_float_from_config("time_between_vhf_pings_in_sec"),
                               self.config.get_int_from_config("vhf_threshold"),
                               self.config.get_float_from_config("vhf_duration"),
                               self.config.get_float_from_config("time_between_vhf_pings_in_sec") * 5 + 0.1)
            if self.use_audio_trigger:
                self.audio.start(use_trigger=True)

        self.trigger_system = TriggerSystem(audio=self.audio, camera_light_controller=self.camera_light_controller)

        self.main_loop()

    # ####################################### helper functions #########################################################

    def __clean_up(self):
        """stops all streams, clean up state and set the gpio to low"""
        if self.use_camera:
            self.camera_light_controller.clean_up()
        if self.use_microphone:
            self.audio.clean_up()
        Helper.print_message("everything is cleaned up", False)

    def signal_handler(self, sig=None, frame=None):
        """cleans all states and streams up and terminates the process"""
        Helper.print_message("You pressed Ctrl+C!", False)
        clean_up_thread = threading.Thread(target=self.__clean_up, args=())
        clean_up_thread.start()
        time.sleep(1)
        quit()

    ############################################# main loop ############################################################
    @staticmethod
    def main_loop():
        """
        get the signals from the microphone and process the audio in case the microphone is in use
        else it is only a aways running dummy loop
        :return: None
        """
        while True:
            time.sleep(1)


if __name__ == "__main__":
    batRecorder = BatRack("pi", "natur", "rteu", "/boot/BatRack.conf")

