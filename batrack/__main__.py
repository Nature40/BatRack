import signal
import time
import threading
import sys
import logging

from batrack.sensors import CameraLightController
from batrack.sensors import Audio
from batrack.sensors import VHF
from batrack.config import ConfigLoader
from batrack.triggersystem import TriggerSystem


logger = logging.getLogger(__name__)


class BatRack:
    def __init__(self, config_file_name: str):
        signal.signal(signal.SIGINT, self.signal_handler)

        self.db_database = "rteu"
        self.db_user = "pi"
        self.db_password = "natur"
        self.config = ConfigLoader(config_file_name)

        self.debug_on = self.config.get_bool("debug")

        self.data_path: str = self.config.get_string("data_path")

        self.use_camera: bool = self.config.get_bool("use_camera")
        self.use_microphone: bool = self.config.get_bool("use_microphone")
        self.use_audio_trigger: bool = self.config.get_bool(
            "use_audio_trigger")
        self.use_vhf_trigger: bool = self.config.get_bool("use_vhf_trigger")
        self.run_continuous: bool = self.config.get_bool("run_continuous")

        self.waiting_time_between_status_updates = self.config.get_int(
            "waiting_time_between_status_updates")

        time.sleep(self.config.get_int("waiting_time_after_start"))

        self.camera_light_controller = None
        self.audio = None
        self.vhf = None

        self.trigger_system = TriggerSystem()

        self.sensors = []

        if self.use_camera:
            self.camera_light_controller = CameraLightController(
                self.config.get_int("led_pin"))
            self.sensors.append(self.camera_light_controller)
            self.trigger_system.set_camera_and_light_controller(
                self.camera_light_controller)
        if self.use_microphone:
            self.audio = Audio(self.data_path,
                               self.config.get_int("audio_threshold_db"),
                               self.config.get_int(
                                   "audio_highpass_frequency"),
                               self.config.get_int(
                                   "ring_buffer_length_in_sec"),
                               self.config.get_int("audio_split"),
                               self.config.get_int(
                                   "audio_min_seconds_follow_up_recording"),
                               self.debug_on,
                               self.trigger_system,
                               self.config.get_float(
                                   "audio_silence_time"),
                               self.config.get_float("audio_noise_time"))
            self.sensors.append(self.audio)
            self.trigger_system.set_audio(self.audio)
        if self.run_continuous:
            if self.use_camera:
                self.camera_light_controller.start()
            if self.use_microphone:
                self.audio.start(use_trigger=False)
        else:
            if self.use_vhf_trigger:
                self.vhf = VHF(self.db_user,
                               self.db_password,
                               self.db_database,
                               self.config.get_list("vhf_frequencies"),
                               self.config.get_int("vhf_frequency_range"),
                               self.config.get_int("vhf_middle_frequency"),
                               self.config.get_int("vhf_inactive_threshold"),
                               self.config.get_float(
                                   "vhf_time_between_pings_in_sec"),
                               self.config.get_int("vhf_threshold"),
                               self.config.get_float("vhf_duration"),
                               self.config.get_float(
                                   "vhf_time_between_pings_in_sec") * 5 + 0.1,
                               self.debug_on,
                               self.trigger_system)
                self.vhf.start()
                self.sensors.append(self.vhf)
            if self.use_audio_trigger:
                self.audio.start(use_trigger=True)

        self.main_loop()

    def __clean_up(self):
        """
        stops all streams, clean up state and set the gpio to low
        :return:
        """
        if self.use_camera:
            self.camera_light_controller.clean_up()
        if self.use_microphone:
            self.audio.clean_up()
        logger.info("everything is cleaned up")

    def signal_handler(self, sig=None, frame=None):
        """
        cleans all states and streams up and terminates the process
        :param sig:
        :param frame:
        :return:
        """
        logger.info("You pressed Ctrl+C!")
        clean_up_thread = threading.Thread(target=self.__clean_up, args=())
        clean_up_thread.start()
        time.sleep(1)
        quit()

    def main_loop(self):
        """
        it is only a always running dummy loop
        :return: None
        """
        while True:
            for sensor in self.sensors:
                status = sensor.get_status()
                for item_name in status.keys():
                    logger.info("sensor: {} {}: {}".format(
                        str(type(sensor)), item_name, status[item_name]))
            time.sleep(self.waiting_time_between_status_updates)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) > 1:
        batRecorder = BatRack(sys.argv[1])
    else:
        batRecorder = BatRack("etc/BatRack.conf")
