#!/bin/bash
cp BatRack.service /etc/systemd/system/BatRack.service
cp start_bat_rack.sh /home/pi/start_bat_rack.sh
chmod +x /home/pi/start_bat_rack.sh
mkdir /home/pi/BatRack/data/
sudo systemctl daemon-reload
sudo systemctl enable BatRack.service
sudo systemctl start BatRack