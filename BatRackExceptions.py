class ConfigParserException:
    def __init__(self, message, config):
        self.message = message
        self.config = config
