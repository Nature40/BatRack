import signal
import time
import threading
import sys
import logging
import configparser
import argparse
import os
import socket
import copy
import datetime
from distutils.util import strtobool

import schedule

from batrack.sensors import CameraAnalysisUnit
from batrack.sensors import AudioAnalysisUnit
from batrack.sensors import VHFAnalysisUnit


logger = logging.getLogger(__name__)


class BatRack(threading.Thread):
    def __init__(self,
                 config,
                 name: str = "default",
                 data_path: str = "data",
                 duty_cycle_s: int = 10,
                 use_vhf: bool = True,
                 use_audio: bool = True,
                 use_camera: bool = True,
                 use_trigger_vhf: bool = True,
                 use_trigger_audio: bool = True,
                 use_trigger_camera: bool = True,
                 always_on: bool = False,
                 **kwargs):
        super().__init__()
        self.name = name

        # add hostname and  data path
        self.data_path = os.path.join(
            data_path, socket.gethostname(), self.__class__.__name__)
        os.makedirs(self.data_path, exist_ok=True)
        logging.debug(f"data path: {self.data_path}")

        # create instance variables
        self.duty_cycle_s = int(duty_cycle_s)
        self._units = []

        # convert boolean config variables
        use_vhf = strtobool(use_vhf) if isinstance(
            use_vhf, str) else bool(use_vhf)
        use_audio = strtobool(use_audio) if isinstance(
            use_audio, str) else bool(use_audio)
        use_camera = strtobool(use_camera) if isinstance(
            use_camera, str) else bool(use_camera)

        use_trigger_vhf = strtobool(use_trigger_vhf) if isinstance(
            use_trigger_vhf, str) else bool(use_trigger_vhf)
        use_trigger_audio = strtobool(use_trigger_audio) if isinstance(
            use_trigger_audio, str) else bool(use_trigger_audio)
        use_trigger_camera = strtobool(use_trigger_camera) if isinstance(
            use_trigger_camera, str) else bool(use_trigger_camera)

        self.always_on = strtobool(always_on) if isinstance(
            always_on, str) else bool(always_on)

        # setup vhf
        self.vhf = None
        if use_vhf:
            self.vhf = VHFAnalysisUnit(
                **config["VHFAnalysisUnit"],
                use_trigger=use_trigger_vhf,
                trigger_callback=self.evaluate_triggers,
                data_path=self.data_path,
            )
            self._units.append(self.vhf)

        # setup audio
        self.audio = None
        if use_audio:
            self.audio = AudioAnalysisUnit(
                **config["AudioAnalysisUnit"],
                use_trigger=use_trigger_audio,
                trigger_callback=self.evaluate_triggers,
                data_path=self.data_path,
            )
            self._units.append(self.audio)

        # setup camera
        self.camera = None
        if use_camera:
            self.camera = CameraAnalysisUnit(
                **config["CameraAnalysisUnit"],
                use_trigger=use_trigger_camera,
                trigger_callback=self.evaluate_triggers,
                data_path=self.data_path,
            )
            self._units.append(self.camera)

        self._running = False
        self._trigger = False

    def evaluate_triggers(self, callback_trigger):
        if self.always_on:
            trigger = True
        else:
            trigger = False

        # if any of the used triggers fires, the system trigger is set
        for unit in self._units:
            logger.debug(
                f"trigger evaluation {unit.__class__.__name__} use_trigger: {unit.use_trigger}, trigger: {unit.trigger}")
            if unit.use_trigger:
                if unit.trigger:
                    trigger = True

        logger.debug(f"trigger evaluation, current state: {trigger}")

        # start / stop recordings if the system trigger changed
        if trigger != self._trigger:
            self._trigger = trigger
            if trigger:
                logger.info(f"System triggered, starting recordings")
                [unit.start_recording() for unit in self._units]
            else:
                logger.info(f"System un-triggered, stopping recordings")
                [unit.stop_recording() for unit in self._units]

        return trigger

    def run(self):
        self._running = True

        # start units
        [unit.start() for unit in self._units]

        # do an initial trigger evaluation, also starts recordings when no trigger is used at all
        self.evaluate_triggers(None)

        # print status reports
        while self._running:
            for unit in self._units:
                status_str = ", ".join(
                    [f"{k}: {'1' if v else '0'}" for k, v in unit.get_status().items()])
                logger.info(f"{unit.__class__.__name__:20s}: {status_str}")

            time.sleep(self.duty_cycle_s)

        logger.info(f"BatRack [{self.name}] finished")

    def stop(self):
        """
        stops all streams, clean up state and set the gpio to low
        :return:
        """
        logger.info(f"Stopping [{self.name}] and respective sensor instances")
        self._running = False

        [unit.stop() for unit in self._units]
        logger.info(f"Finished cleaning [{self.name}] sensors")

        self.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Evaluate sensors for active bats and trigger recordings.")
    parser.add_argument("configfile",
                        nargs='?',
                        default="etc/BatRack.conf")

    args = parser.parse_args()

    # read config file
    config = configparser.ConfigParser()
    config.read(args.configfile)

    # configure logging
    logging_level = config["BatRack"].get("logging_level", "INFO")
    logging.basicConfig(level=logging_level)
    logging.debug(f"logging level set to {logging_level}")

    lock = threading.Lock()
    instance = None

    def create_and_run(config, k, run_config):
        logger.info(f"[{k}] waiting for remaining instance")
        lock.acquire()

        logger.info(f"[{k}] creating instance")
        global instance
        instance = BatRack(config, name=k, **run_config)
        instance.start()
        logger.info(f"[{k}] started")

    def stop_and_remove(k):
        logger.info(f"[{k}] stopping instance")
        global instance
        if instance:
            instance.stop()
            instance = None
            lock.release()
        logger.info(f"[{k}] stopped")

    config_has_runs = 0
    now = datetime.datetime.now()

    # iterate through runs an enter schedulings
    for k in config.keys():
        if not k.startswith("run"):
            continue

        run_config = copy.deepcopy(config["BatRack"])
        run_config.update(config[k])

        try:
            start_s = schedule.every().day.at(run_config["start"])
            stop_s = schedule.every().day.at(run_config["stop"])

            logger.info(
                f"[{k}] running from {run_config['start']} to {run_config['stop']}")

            start_s.do(create_and_run, config, k, run_config)
            stop_s.do(stop_and_remove, k)

            if now.time() > start_s.at_time:
                if now.time() < stop_s.at_time:
                    logger.info(f"[{k}] starting run now (in interval)")
                    create_and_run(config, k, run_config)

            config_has_runs += 1

        except KeyError as e:
            logger.error(
                f"[{k}] is missing a {e} time, please check the configuration file ({args.configfile}).")
            sys.exit(1)
        except schedule.ScheduleValueError as e:
            logger.error(
                f"[{k}] {e}, please check the configuration file ({args.configfile}).")
            sys.exit(1)

    running = True

    # create a signal handler to terminate cleanly
    def signal_handler(sig=None, frame=None):
        logger.info("Caught SIGINT, terminating execution...")
        global running
        running = False

        stop_and_remove("SIGINT")

    signal.signal(signal.SIGINT, signal_handler)

    # start the run scheduling or run continuously
    if config_has_runs:
        logger.info(f"starting run scheduling")
        while running:
            schedule.run_pending()
            time.sleep(1)
    else:
        logger.info("No valid runs have been defined, running continuously.")
        create_and_run(config, "continuous", config["BatRack"])
