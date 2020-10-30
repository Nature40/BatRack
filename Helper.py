import datetime
import sys


def get_time():
    """returns the current datetime as string"""
    return str(datetime.datetime.now())


def print_message(message: str, is_debug: bool = False, debug_on: bool = False):
    """helper function for consistent output"""
    if is_debug and debug_on:
        print("DEBUG: " + get_time() + message)
    if not is_debug:
        print(get_time() + " " + message)
    sys.stdout.flush()
