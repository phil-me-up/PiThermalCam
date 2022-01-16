#!/bin/sh
# launcher.sh
# launches thermal cam
# not that this might be used by a crontab on startup (crontab -u pi -e)
# also note this should use chmod 755

cd /
cd home/pi/Documents/PhilPI/PiThermalCam/pithermalcam
python3 web_server_with_screen.py
cd /