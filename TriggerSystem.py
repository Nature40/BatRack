import Helper


class TriggerSystem:
    def __init__(self):
        self.count_recorder = 0
        self.audio = None
        self.camera_and_light_controller = None

    def set_audio(self, audio):
        self.audio = audio

    def set_camera_and_light_controller(self, camera_and_light_controller):
        self.camera_and_light_controller = camera_and_light_controller

    def start_sequence_audio(self):
        """
        start all parts of the system to record a bat if an audio trigger appears
        :return:
        """
        if self.__check_recorder_at_start() and self.camera_and_light_controller is not None:
            self.camera_and_light_controller.start()

    def stop_sequence_audio(self):
        """
        stop all parts of the system which are used to record bats
        :return:
        """
        if self.__check_recorder_at_stop():
            self.camera_and_light_controller.stop()

    def start_sequence_camera(self):
        """
        start all parts of the system to record a bat
        :return:
        """
        if self.__check_recorder_at_start() and self.camera_and_light_controller is not None:
            self.audio.start(use_trigger=False)

    def stop_sequence_camera(self):
        """
        stop all parts of the system which are used to record bats
        :return:
        """
        if self.__check_recorder_at_stop() and self.audio is not None:
            self.audio.stop(use_trigger=False)

    def start_sequence_vhf(self):
        """
        start all parts of the system to record a bat
        :return:
        """
        if self.__check_recorder_at_start() and self.camera_and_light_controller is not None and self.audio is not None:
            self.audio.start(use_trigger=False)
            self.camera_and_light_controller.start()

    def stop_sequence_vhf(self):
        """
        stop all parts of the system which are used to record bats
        :return:
        """
        if self.__check_recorder_at_stop() and self.camera_and_light_controller is not None and self.audio is not None:
            self.audio.stop(use_trigger=False)
            self.camera_and_light_controller.stop()

    def __check_recorder_at_start(self) -> bool:
        """
        increases the number of sensor which want to record at the time
        :return: if the recording should be started
        """
        self.count_recorder += 1
        Helper.print_message("start {}".format(self.count_recorder), False)
        return self.count_recorder == 1

    def __check_recorder_at_stop(self) -> bool:
        """
        decreases the number of sensor which want to record at the time
        :return: if the recording should be stopped
        :return:
        """
        self.count_recorder -= 1
        return self.count_recorder == 0