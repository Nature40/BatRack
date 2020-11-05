import mysql.connector as mariadb
import numpy as np
import threading
import datetime
import copy
import time
import Helper
from collections import defaultdict
from Sensors.Sensor import Sensor


class VHF(Sensor):
    def __init__(self,
                 db_user,
                 db_password,
                 db_database,
                 vhf_frequencies,
                 frequency_range_for_vhf_frequency,
                 vhf_middle_frequency,
                 vhf_inactive_threshold,
                 time_between_vhf_pings_in_sec,
                 vhf_threshold,
                 vhf_duration,
                 observation_time_for_ping_in_sec,
                 trigger_system):
        self.db_user = db_user
        self.db_password = db_password
        self.db_database = db_database
        self.vhf_frequencies = vhf_frequencies
        self.frequency_range_for_vhf_frequency = frequency_range_for_vhf_frequency
        self.vhf_middle_frequency = vhf_middle_frequency
        self.time_between_vhf_pings_in_sec = time_between_vhf_pings_in_sec
        self.vhf_threshold = vhf_threshold
        self.trigger_system = trigger_system
        self.vhf_duration = vhf_duration
        self.observation_time_for_ping_in_sec = observation_time_for_ping_in_sec
        self.currently_active_vhf_frequencies = copy.deepcopy(self.vhf_frequencies)
        self.vhf_inactive_threshold = vhf_inactive_threshold
        self.stopped = False
        self.vhf_recording = False
        self.trigger_events_since_last_status = 0
        self.present_and_active_bats = []

    def stop(self):
        self.stopped = True

    def start(self):
        check_signal_for_active_bats = threading.Thread(target=self.__check_vhf_signal_for_active_bats, args=())
        check_signal_for_active_bats.start()
        check_frequencies_for_inactivity = threading.Thread(target=self.__check_vhf_frequencies_for_inactivity, args=())
        check_frequencies_for_inactivity.start()

    def get_status(self):
        """
        delivers some nice information about the audio sensor since the last call
        :return:
        """
        present_and_inactive_bats = [x for x in self.vhf_frequencies if x not in self.currently_active_vhf_frequencies]
        absent_bats = [x for x in self.vhf_frequencies if
                       x not in self.currently_active_vhf_frequencies or self.present_and_active_bats]
        return_values = {"running": not self.stopped,
                         "recording": self.vhf_recording,
                         "trigger events": self.trigger_events_since_last_status,
                         "bats present and inactive": present_and_inactive_bats,
                         "bats present and active": self.present_and_active_bats,
                         "bats absent": absent_bats}
        self.trigger_events_since_last_status = 0
        return return_values

    def __query_for_frequency_and_signal_strength(self,
                                                  signal_threshold: int,
                                                  duration: float,
                                                  timestamp: datetime.datetime):
        """
        :param signal_threshold: signals must have a higher peak power than the threshold is
        :param duration: the duration of the signal must be less than this duration
        :param timestamp: the signal must be newer the the timestamp
        :return: returns the signal frequency and peak power for all matching signals
        """
        query = "SELECT signal_freq, max_signal FROM signals WHERE max_signal > %s AND duration < %s AND timestamp > %s"
        try:
            maria_db = mariadb.connect(user=self.db_user, password=self.db_password, database=self.db_database)
            cursor = maria_db.cursor()
            cursor.execute(query, (signal_threshold, duration, timestamp))
            return_values = cursor.fetchall()
            cursor.close()
            maria_db.close()
            return return_values
        except Exception as e:
            Helper.print_message("Error for query: {} with error: {}".format(query, e), False)

    def __is_frequency_currently_active(self, frequency: int):
        """
        True if the given frequency is in the frequency range of currently active frequencies else False
        :param frequency: frequency to check
        :return: bool
        """
        for wanted_frequency in self.currently_active_vhf_frequencies:
            if wanted_frequency - self.frequency_range_for_vhf_frequency < \
                    (frequency + self.vhf_middle_frequency / 1000) < \
                    wanted_frequency + self.frequency_range_for_vhf_frequency:
                return True
        return False

    def __is_frequency_a_bat_frequency(self, frequency: int):
        """
        True if the given frequency is in the frequency range of any of the potential bat frequencies else False
        :param frequency: frequency to check
        :return: bool
        """
        for wanted_frequency in self.vhf_frequencies:
            if wanted_frequency - self.frequency_range_for_vhf_frequency < \
                    (frequency + self.vhf_middle_frequency / 1000) \
                    < wanted_frequency + self.frequency_range_for_vhf_frequency:
                return True
        return False

    def __get_matching_bat_frequency(self, frequency: int):
        """
        :param frequency: given frequency of signal
        :return: the matching frequency out of self.config.vhf_frequencies
        """
        for wanted_frequency in self.vhf_frequencies:
            if wanted_frequency - self.frequency_range_for_vhf_frequency < \
                    (frequency + self.vhf_middle_frequency / 1000) < \
                    wanted_frequency + self.frequency_range_for_vhf_frequency:
                return wanted_frequency

    def __check_vhf_signal_for_active_bats(self):
        """
        an always running thread for the continuous check of all frequencies for activity
        if activity is detected the thread starts the recording
        """
        last_vhf_ping = datetime.datetime.now()
        while True:
            try:
                current_round_check = False
                if self.stopped:
                    break
                now = datetime.datetime.utcnow()
                query_results = self.__query_for_frequency_and_signal_strength(self.vhf_threshold,
                                                              self.vhf_duration,
                                                              now - datetime.timedelta(
                                                              seconds=self.observation_time_for_ping_in_sec))
                now = datetime.datetime.utcnow()
                self.present_and_active_bats = []
                for result in query_results:
                    frequency, _ = result
                    if self.__is_frequency_currently_active(frequency):
                        self.present_and_active_bats.append(self.__get_matching_bat_frequency(frequency))
                        current_round_check = True
                        last_vhf_ping = now

                if current_round_check:
                    if not self.vhf_recording:
                        self.trigger_system.start_sequence_vhf()
                        self.vhf_recording = True
                        Helper.print_message("vhf_recording start", False)
                        self.trigger_events_since_last_status += 1
                    time.sleep(1)
                else:
                    if self.vhf_recording and (now - last_vhf_ping) > datetime.timedelta(
                            seconds=self.observation_time_for_ping_in_sec):
                        self.trigger_system.start_sequence_vhf()
                        self.vhf_recording = False
                        Helper.print_message("vhf_recording stop", False)
                    time.sleep(0.2)
            except Exception as e:
                Helper.print_message("Error in check_vhf_signal_for_active_bats: {}".format(e), False)

    def __check_vhf_frequencies_for_inactivity(self):
        """
        an always running thread for continuous adding
        and removing frequencies from the currently active frequencies list
        """
        while True:
            try:
                if self.stopped:
                    break
                now = datetime.datetime.utcnow()
                query_results = self.__query_for_frequency_and_signal_strength(self.vhf_threshold,
                                                                               self.vhf_duration,
                                                                               now - datetime.timedelta(seconds=60))

                signals = defaultdict(list)
                for result in query_results:
                    frequency, signal_strength = result
                    if self.__is_frequency_a_bat_frequency(frequency):
                        signals[self.__get_matching_bat_frequency(frequency)].append(signal_strength)

                for frequency in signals.keys():
                    if len(signals[frequency]) > 10 \
                            and np.std(signals[frequency]) < self.vhf_inactive_threshold  \
                            and frequency in self.currently_active_vhf_frequencies:
                        Helper.print_message("remove frequency: {}".format(frequency), False)
                        self.currently_active_vhf_frequencies.remove(frequency)
                    elif np.std(signals[frequency]) > self.vhf_inactive_threshold \
                            and frequency not in self.currently_active_vhf_frequencies:
                        if frequency not in self.currently_active_vhf_frequencies:
                            Helper.print_message("add frequency: {}".format(frequency), False)
                            self.currently_active_vhf_frequencies.append(frequency)

                for frequency in self.vhf_frequencies:
                    if frequency not in signals.keys() and frequency not in self.currently_active_vhf_frequencies:
                        Helper.print_message("add frequency: {}".format(frequency), False)
                        self.currently_active_vhf_frequencies.append(frequency)
                time.sleep(10)
            except Exception as e:
                Helper.print_message("Error in check_vhf_frequencies_for_inactivity: ".format(e), False)
