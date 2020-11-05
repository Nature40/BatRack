import RPi.GPIO as GPIO
import Sensors.Sensor as Sensor


class CameraLightController(Sensor):
    def __init__(self, led_pin):
        self.led_pin = led_pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)
        self.triggered_events_since_last_status = 0
        self.current_state = False

    def start(self):
        self.current_state = True
        self.__start_led()
        self.__start_camera()

    def stop(self):
        self.current_state = False
        self.__stop_led()
        self.__stop_camera()

    def clean_up(self):
        """
        clean up the GPIO state and close the connection
        :return:
        """
        GPIO.output(self.led_pin, GPIO.LOW)
        GPIO.cleanup()

    def get_status(self) -> dict:
        """
        delivers some nice information about the audio sensor since the last call
        :return:
        """
        return_values = {"trigger events": self.triggered_events_since_last_status, "is on": self.current_state}
        self.triggered_events_since_last_status = 0
        return return_values


    @staticmethod
    def __start_camera():
        """
        start the camera via file trigger to RPi_Cam_Web_Interface
        :return:
        """
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("1")

    @staticmethod
    def __stop_camera():
        """
        stop the camera via file trigger to RPi_Cam_Web_Interface
        :return:
        """
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("0")

    def __start_led(self):
        """
        start the led spot via GPIO
        :return:
        """
        GPIO.output(self.led_pin, GPIO.HIGH)

    def __stop_led(self):
        """
        stop the led spot via GPIO
        :return:
        """
        GPIO.output(self.led_pin, GPIO.LOW)

