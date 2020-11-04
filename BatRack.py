import signal
import time
import threading
from Sensors.Audio import Audio
from Sensors.VHF import VHF
from Sensors.CameraLightController import CameraLightController
from Config import Config
from TriggerSystem import TriggerSystem
import Helper


class BatRack(object):
    def __init__(self, db_user: str, db_password: str, db_database: str, config_file_name: str):

        self.debug_on = False
        signal.signal(signal.SIGINT, self.signal_handler)

        self.db_user = db_user
        self.db_password = db_password
        self.db_database = db_database
        self.config = Config(config_file_name)

        self.data_path: str = self.config.get_string("data_path")

        self.use_camera: bool = self.config.get_bool("use_camera")
        self.use_microphone: bool = self.config.get_bool("use_microphone")
        self.use_audio_trigger: bool = self.config.get_bool("use_audio_trigger")
        self.use_vhf_trigger: bool = self.config.get_bool("use_vhf_trigger")
        self.run_continuous: bool = self.config.get_bool("run_continuous")

        time.sleep(self.config.get_int("waiting_time_after_start"))

        self.camera_light_controller = None
        self.audio = None
        self.vhf = None

        self.trigger_system = TriggerSystem()

        if self.use_camera:
            self.camera_light_controller = CameraLightController(self.config.get_int("led_pin"))
            self.trigger_system.set_camera_and_light_controller(self.camera_light_controller)
        if self.use_microphone:
            self.audio = Audio(self.data_path,
                               self.config.get_int("threshold_dbfs"),
                               self.config.get_int("highpass_frequency"),
                               self.config.get_int("ring_buffer_length_in_sec"),
                               self.config.get_int("audio_split"),
                               self.config.get_int("min_seconds_for_audio_recording"),
                               self.debug_on,
                               self.trigger_system)
            self.trigger_system.set_audio(self.audio)
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
                               self.config.get_list("vhf_frequencies"),
                               self.config.get_int("frequency_range_for_vhf_frequency"),
                               self.config.get_int("vhf_middle_frequency"),
                               self.config.get_int("vhf_inactive_threshold"),
                               self.config.get_float("time_between_vhf_pings_in_sec"),
                               self.config.get_int("vhf_threshold"),
                               self.config.get_float("vhf_duration"),
                               self.config.get_float("time_between_vhf_pings_in_sec") * 5 + 0.1,
                               self.trigger_system)
            if self.use_audio_trigger:
                self.audio.start(use_trigger=True)



        self.main_loop()

    # ####################################### helper functions #########################################################

    def __clean_up(self):
        """
        stops all streams, clean up state and set the gpio to low
        :return:
        """
        if self.use_camera:
            self.camera_light_controller.clean_up()
        if self.use_microphone:
            self.audio.clean_up()
        Helper.print_message("everything is cleaned up", False)

    def signal_handler(self, sig=None, frame=None):
        """
        cleans all states and streams up and terminates the process
        :param sig:
        :param frame:
        :return:
        """
        Helper.print_message("You pressed Ctrl+C!", False)
        clean_up_thread = threading.Thread(target=self.__clean_up, args=())
        clean_up_thread.start()
        time.sleep(1)
        quit()

    ############################################# main loop ############################################################
    @staticmethod
    def main_loop():
        """
        it is only a always running dummy loop
        :return: None
        """
        while True:
            time.sleep(1)
            Helper.print_message("sleeping")


if __name__ == "__main__":
    batRecorder = BatRack("pi", "natur", "rteu", "/boot/BatRack_test.conf")

