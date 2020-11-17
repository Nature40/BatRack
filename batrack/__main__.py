import signal
import time
import threading
import sys
import logging
import configparser
import os

from batrack.sensors import CameraLightController
from batrack.sensors import Audio
from batrack.sensors import VHF
from batrack.triggersystem import TriggerSystem


logger = logging.getLogger(__name__)


class BatRack:
    def __init__(self,
                 config,
                 logging_level=logging.INFO,
                 data_path: str = "data",
                 use_camera: bool = True,
                 use_microphone: bool = True,
                 use_audio_trigger: bool = True,
                 use_vhf_trigger: bool = True,
                 always_on: bool = False,
                 duty_cycle_s: int = 10,
                 **kwargs):
        signal.signal(signal.SIGINT, self.signal_handler)

        self.db_database = "rteu"
        self.db_user = "pi"
        self.db_password = "natur"

        logging.basicConfig(level=logging_level)
        logging.debug(f"logging level set to {logging_level}")

        self.data_path = data_path
        os.makedirs(self.data_path, exist_ok=True)
        logging.debug(f"data path: {self.data_path}")

        # self.use_camera: bool = self.config.get_bool("use_camera")
        # self.use_microphone: bool = self.config.get_bool("use_microphone")
        # self.use_audio_trigger: bool = self.config.get_bool(
        #     "use_audio_trigger")
        # self.use_vhf_trigger: bool = self.config.get_bool("use_vhf_trigger")
        # self.run_continuous: bool = self.config.get_bool("run_continuous")

        self.duty_cycle_s = int(duty_cycle_s)

        # self.camera = None
        # self.audio = None
        # self.vhf = None

        self.trigger_system = TriggerSystem()

        self.sensors = []

        if use_camera:
            self.camera = CameraLightController(**config["camera"])
            self.sensors.append(self.camera)
            self.trigger_system.set_camera(self.camera)

        if use_microphone:
            self.audio = Audio(
                self.data_path,
                self.trigger_system,
                **config["audio"],
            )
            self.sensors.append(self.audio)
            self.trigger_system.set_audio(self.audio)

        if always_on:
            if self.camera:
                self.camera.start()
            if self.audio:
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
        if self.camera:
            self.camera.clean_up()
        if self.audio:
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
            time.sleep(self.duty_cycle_s)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        config_path = BatRack(sys.argv[1])
    else:
        config_path = "etc/BatRack.conf"

    config = configparser.ConfigParser()
    config.read(config_path)

    batRecorder = BatRack(config, **config["core"])
