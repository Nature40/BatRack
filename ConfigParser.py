class ConfigParser:
    def __init__(self):
        pass

    def __get_bool_from_config(self, parameter_name:str):
        return self.config.getboolean(parameter_name)


    def __get_int_from_config(self, parameter_name:str):
        try:
            return int(self.config[parameter_name])
        except ValueError as e:
            self.print_message("Error converting config: {} parameter_name: {} is no int ".format(e, parameter_name), False)
            self.__clean_up()

    def __get_float_from_config(self, parameter_name:str):
        try:
            return float(self.config[parameter_name])
        except ValueError as e:
            self.print_message("Error converting config: {} parameter_name: {} is no list".format(e, parameter_name), False)
            self.__clean_up()

    def __get_list_from_config(self, parameter_name:str):
        try:
            return json.loads(self.config["vhf_frequencies"])
        except ValueError as e:
            self.print_message("Error converting config: {} parameter_name: {} is no float".format(e, parameter_name), False)
            self.__clean_up()