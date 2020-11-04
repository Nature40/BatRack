import RPi.GPIO as GPIO


class CameraLightController:
    def __init__(self, led_pin):
        self.led_pin = led_pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.led_pin, GPIO.OUT)
        GPIO.output(self.led_pin, GPIO.LOW)

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

    def start(self):
        self.__start_led()
        self.__start_camera()

    def stop(self):
        self.__stop_led()
        self.__stop_camera()

    def clean_up(self):
        GPIO.output(self.led_pin, GPIO.LOW)
        GPIO.cleanup()
