copy the content of BatRecorder.service to /etc/systemd/system/BatRack.service so we create a new system service 
```
sudo cp BatRack.service /etc/systemd/system/BatRack.service
```

copy the bash file start_bat_rack.sh to the home directory 
```
cp start_bat_rack.sh /home/pi/start_bat_rack.sh
```

make it executable
```
sudo chmod +x /home/pi/start_bat_rack.sh
```

and create a data folder to the BatRecorder
```
mkdir /home/pi/BatRack/data/
```

enable the system service 
```
sudo systemctl daemon-reload
sudo systemctl enable BatRack.service
sudo systemctl start BatRack
```

check log form the BatRecorder with:
```
journalctl -fu BatRack
```