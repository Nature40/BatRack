from configparser import ConfigParser
from BatRackExceptions import ConfigParserException
import json


class Config:
    def __init__(self, complete_file_name):
        self.config_object = ConfigParser()
        self.config_object.read(complete_file_name)
        self.config = self.config_object["CONFIG"]

    def get_bool_from_config(self, parameter_name: str):
        return self.config.getboolean(parameter_name)

    def get_int_from_config(self, parameter_name: str):
        try:
            return int(self.config[parameter_name])
        except ValueError as e:
            raise ConfigParserException("Error converting config at parameter_name: {} to int ".format(parameter_name),
                                        self.config)

    def get_float_from_config(self, parameter_name: str):
        try:
            return float(self.config[parameter_name])
        except ValueError as e:
            raise ConfigParserException("Error converting config at parameter_name: {} to float".format(parameter_name),
                                        self.config)

    def get_list_from_config(self, parameter_name: str):
        try:
            return json.loads(self.config["vhf_frequencies"])
        except ValueError as e:
            raise ConfigParserException("Error converting config at parameter_name: {} to list".format(parameter_name),
                                        self.config)
