from configparser import ConfigParser
from BatRackExceptions import ConfigParserException
import json


class Config:
    def __init__(self, complete_file_name):
        self.config_object = ConfigParser()
        self.config_object.read(complete_file_name)
        self.config = self.config_object["CONFIG"]

    def get_bool(self, parameter_name: str) -> bool:
        """
        :param parameter_name: the name of the config element
        :return: the element of the config as bool if possible
        """
        return self.config.getboolean(parameter_name)

    def get_int(self, parameter_name: str) -> int:
        """
        :param parameter_name: the name of the config element
        :return: the element of the config as int if possible
        """
        try:
            return int(self.config[parameter_name])
        except ValueError:
            raise ConfigParserException("Error converting config at parameter_name: {} to int ".format(parameter_name),
                                        self.config)

    def get_float(self, parameter_name: str) -> float:
        """
        :param parameter_name: the name of the config element
        :return: the element of the config as float if possible
        """
        try:
            return float(self.config[parameter_name])
        except ValueError:
            raise ConfigParserException("Error converting config at parameter_name: {} to float".format(parameter_name),
                                        self.config)

    def get_list(self, parameter_name: str) -> list:
        """
        :param parameter_name: the name of the config element
        :return: the element of the config as list if possible
        """
        try:
            return json.loads(self.config["vhf_frequencies"])
        except ValueError:
            raise ConfigParserException("Error converting config at parameter_name: {} to list".format(parameter_name),
                                        self.config)

    def get_string(self, parameter_name: str) -> str:
        """
        :param parameter_name: the name of the config element
        :return: the element of the config as string if possible
        """
        return self.config[parameter_name]
