#!/bin/bash
sudo cp BatRack.service /etc/systemd/system/BatRack.service
chmod +x /home/pi/BatRack/start_bat_rack.sh
sudo mkdir /home/pi/BatRack/data/
sudo systemctl daemon-reload
sudo systemctl enable BatRack.service
sudo systemctl start BatRack