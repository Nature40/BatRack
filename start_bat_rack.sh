#!/bin/bash
cd /home/pi/RPi_Cam_Web_Interface
./debug.sh&
cd /home/pi/BatRack/data
now=`date +"%Y_%m_%d-%H:%M"`
python3 ../BatRack.py > /home/pi/BatRack/data/$now.log 2>&1