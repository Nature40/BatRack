class Sensor:
    def start(self):
        raise NotImplemented

    def stop(self):
        raise NotImplemented

    def clean_up(self):
        raise NotImplemented

    def get_status(self) -> dict:
        raise NotImplemented
