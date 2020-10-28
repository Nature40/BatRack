import RPi.GPIO as GPIO

class CameraLighController:
    def __init__(self, led_pin):
        self.led_pin = led_pin

    def start_camera(self):
        '''start the camera if the camera should be used'''
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("1")

    def stop_camera(self):
        '''stop the camera if the camera should be used'''
        with open("/var/www/html/FIFO1", "w") as f:
            f.write("0")

    def start_led(self):
        '''start the led spot if the led spot should be used'''
        GPIO.output(self.led_pin, GPIO.HIGH)

    def stop_led(self):
        '''stop the led spot if the led spot should be used'''
        GPIO.output(self.led_pin, GPIO.LOW)