import Helper
import Sensors.Audio as Audio
import Sensors.CameraLightController as CameraLightController


class TriggerSystem:
    def __init__(self, audio: Audio, camera_light_controller: CameraLightController):
        self.count_recorder = 0
        self.audio = audio
        self.camera_light_controller = camera_light_controller

    def __check_recorder_at_start(self):
        """
        increases the number of sensor which want to record at the time
        :return: if the recording should be started
        """
        self.count_recorder += 1
        Helper.print_message("start {}".format(self.count_recorder), False)
        return self.count_recorder == 1

    def __check_recorder_at_stop(self):
        """
        decreases the number of sensor which want to record at the time
        :return: if the recording should be stopped
        :return:
        """
        self.count_recorder -= 1
        return self.count_recorder == 0

    def start_sequence_audio(self):
        """start all parts of the system to record a bat if an audio trigger appears"""
        if self.__check_recorder_at_start():
            self.camera_light_controller.start()

    def stop_sequence_audio(self):
        """stop all parts of the system which are used to record bats"""
        if self.__check_recorder_at_stop():
            self.camera_light_controller.stop()

    def start_sequence_camera(self):
        """start all parts of the system to record a bat"""
        if self.__check_recorder_at_start():
            self.audio.start()

    def stop_sequence_camera(self):
        """stop all parts of the system which are used to record bats"""
        if self.__check_recorder_at_stop():
            self.audio.stop()

    def start_sequence_vhf(self):
        """start all parts of the system to record a bat"""
        if self.__check_recorder_at_start():
            self.audio.start()
            self.camera_light_controller.start()

    def stop_sequence_vhf(self):
        """stop all parts of the system which are used to record bats"""
        if self.__check_recorder_at_stop():
            self.audio.stop()
            self.camera_light_controller.stop()

