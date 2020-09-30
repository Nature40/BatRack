#!/bin/bash
cp BatRecorder.service /etc/systemd/system/BatRecorder.service
cp start_bat_recorder.sh /home/pi/start_bat_recorder.sh
chmod +x /home/pi/start_bat_recorder.sh
mkdir /home/pi/BatRecorder/data/
sudo systemctl daemon-reload
sudo systemctl enable BatRecorder.service
sudo systemctl start BatRecorder