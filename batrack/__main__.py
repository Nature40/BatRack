import signal
import time
import threading
import sys
import logging
import configparser
import os
import socket
import copy

import schedule
from pytimeparse import parse as parse_time

from batrack.sensors import CameraLightController
from batrack.sensors import Audio
from batrack.sensors import VHF
from batrack.triggersystem import TriggerSystem


logger = logging.getLogger(__name__)


class BatRack(threading.Thread):
    def __init__(self,
                 config,
                 name: str = "default",
                 data_path: str = "data",
                 use_camera: bool = True,
                 use_microphone: bool = True,
                 duty_cycle_s: int = 10,
                 use_audio_trigger: bool = True,
                 use_vhf_trigger: bool = True,
                 always_on: bool = False,
                 **kwargs):
        super().__init__()
        self.name = name

        # legacy database connection
        self.db_database = "rteu"
        self.db_user = "pi"
        self.db_password = "natur"

        # check data path
        self.data_path = os.path.join(
            data_path, socket.gethostname(), self.__class__.__name__)
        os.makedirs(self.data_path, exist_ok=True)
        logging.debug(f"data path: {self.data_path}")

        # create instance variables
        self.duty_cycle_s = int(duty_cycle_s)
        self.sensors = []

        self.trigger_system = TriggerSystem()

        # setup camera
        if use_camera:
            self.camera = CameraLightController(**config["camera"])
            self.trigger_system.set_camera(self.camera)
            self.sensors.append(self.camera)

        # setup microphone
        if use_microphone:
            self.audio = Audio(
                self.data_path,
                self.trigger_system,
                **config["audio"],
            )
            self.trigger_system.set_audio(self.audio)
            self.sensors.append(self.audio)

        self._always_on = always_on
        self._use_vhf_trigger = use_vhf_trigger
        self._use_audio_trigger = use_audio_trigger

        self._running = False

    def run(self):
        self._running = True

        # start recording straight, if always_on mode is selected
        if self._always_on:
            if self.camera:
                self.camera.start()
            if self.audio:
                self.audio.start(use_trigger=False)

        # regular trigger-based mode
        else:
            if self._use_vhf_trigger:
                self.vhf = VHF(self.trigger_system, **config["vhf"])
                self.sensors.append(self.vhf)
                self.vhf.start()

            if self._use_audio_trigger:
                self.audio.start(use_trigger=True)

        # print status reports
        while self._running:
            for sensor in self.sensors:
                status = sensor.get_status()
                for s_name, s_value in status.items():
                    logger.info(
                        f"sensor: {sensor.__class__.__name__} - {s_name}: {s_value}")

            time.sleep(self.duty_cycle_s)

        logger.info(f"BatRack '{self.name}' finished")

    def stop(self):
        """
        stops all streams, clean up state and set the gpio to low
        :return:
        """
        logger.info(f"Stopping [{self.name}]")
        self.running = False

        if self.camera:
            self.camera.clean_up()
        if self.audio:
            self.audio.clean_up()

        logger.info(f"Finished cleaning [{self.name}] sensors")
        self.join()


if __name__ == "__main__":

    if len(sys.argv) > 1:
        config_path = BatRack(sys.argv[1])
    else:
        config_path = "etc/BatRack.conf"

    # read config file
    config = configparser.ConfigParser()
    config.read(config_path)

    # configure logging
    logging_level = config["core"].get("logging_level", "INFO")
    logging.basicConfig(level=logging_level)
    logging.debug(f"logging level set to {logging_level}")

    # instances = {}

    # def create_and_run(config, k, run_config):
    #     logger.info(f"[{k}] creating instance")
    #     r = BatRack(config, name=k, **run_config)
    #     instances[k] = r
    #     r.start()
    #     logger.info(f"[{k}] started")

    # def stop_and_remove(k):
    #     logger.info(f"[{k}] stopping instance")
    #     r = instances.pop(k)
    #     r.stop()
    #     logger.info(f"[{k}] stopped")

    # # iterate through runs
    # for k in config.keys():
    #     if not k.startswith("run"):
    #         continue

    #     run_config = copy.deepcopy(config["core"])
    #     run_config.update(config[k])

    #     try:
    #         start_s = schedule.every().day.at(run_config["start"])
    #         stop_s = schedule.every().day.at(run_config["stop"])

    #         logger.info(
    #             f"[{k}] running from {run_config['start']} to {run_config['stop']}")
    #     except KeyError as e:
    #         logger.warning(f"[{k}] is missing a {e} time, skipping...")
    #         continue
    #     except schedule.ScheduleValueError as e:
    #         logger.warning(f"[{k}] {e}, skipping...")
    #         continue

    # start_s.do(create_and_run, config, k, run_config)
    # stop_s.do(stop_and_remove, k)

    # if not "start" in run_config:
    #     continue
    # if not "stop" in run_config:
    #     logger.warning(f"[{k}] is missing a stop time, skipping...")
    #     continue

    # running = True

    # def signal_handler(sig=None, frame=None):
    #     logger.info("Caught SIGINT, terminating execution...")
    #     for name, instance in instances.items():
    #         logger.info(f"Stopping [{name}]")
    #         instance.stop()

    #     sys.exit(0)

    # signal.signal(signal.SIGINT, signal_handler)

    def _trigger_callback(value):
        logger.info(f"trigger {value}")
        # if value:
        #     a.start_recording()
        # else:
        #     a.stop_recording()

    # logger.info("Creating audio")
    # a = Audio(**config["audio"],
    #           trigger_callback=_trigger_callback, use_trigger=True)

    # logger.info("Starting audio thread")
    # a.start()
    # time.sleep(10)

    # a.start_recording()
    # time.sleep(20)
    # a.stop_recording()

    # logger.info("Stopping audio thread")
    # a.stop()

    logger.info("Creating vhf")
    v = VHF(**config["vhf"],
            trigger_callback=_trigger_callback,
            use_trigger=True)

    logger.info("Starting vhf thread")
    v.start()
    time.sleep(65)

    logger.info("Stopping vhf thread")
    v.stop()

    # while True:
    #     schedule.run_pending()
    #     time.sleep(1)
