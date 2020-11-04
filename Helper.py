import datetime
import sys


def get_time() -> str:
    """
    returns the current datetime as string
    :return:
    """
    return str(datetime.datetime.now())


def print_message(message: str, is_debug: bool = False, debug_on: bool = False):
    """
    helper function for consistent output
    :param message:
    :param is_debug:
    :param debug_on:
    :return:
    """
    if is_debug and debug_on:
        print("DEBUG: " + get_time() + message)
    if not is_debug:
        print(get_time() + " " + message)
    sys.stdout.flush()
