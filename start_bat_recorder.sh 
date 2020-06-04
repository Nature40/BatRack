#!/bin/bash
cd /home/pi/BatRecorder/RPi_Cam_Web_Interface
./debug.sh&
cd /home/pi/BatRecorder/data
now=`date +"%Y_%m_%d-%H:%M"`
python3 ../BatRecorder.py > /home/pi/BatRecorder/data/$now.log 2>&1