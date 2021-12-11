# -*- coding: utf-8 -*-
#!/usr/bin/python3
##################################
# Flask web server for MLX90640 Thermal Camera w Raspberry Pi
# If running directly, run from root folder, not pithermalcam folder
##################################
try:  # If called as an imported module
	from pi_therm_cam import pithermalcam
except:  # If run directly
	from pi_therm_cam import pithermalcam
from flask import Response, request
from flask import Flask
from flask import render_template
import threading
import time, socket, logging, traceback
import cv2

import sys
import time

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

import ST7789

# Set up Logger
logging.basicConfig(filename='pithermcam.log',filemode='a',
					format='%(asctime)s %(levelname)-8s [%(filename)s:%(name)s:%(lineno)d] %(message)s',
					level=logging.WARNING,datefmt='%d-%b-%y %H:%M:%S')
logger = logging.getLogger(__name__)

# initialize the output frame and a lock used to ensure thread-safe exchanges of the output frames (useful when multiple browsers/tabs are viewing the stream)
outputFrame = None
thermcam = None
lock = threading.Lock()

#  display
disp = None

# initialize a flask object
app = Flask(__name__)

@app.route("/")
def index():
	# return the rendered template
	return render_template("index.html")

#background processes happen without any refreshing (for button clicks)
@app.route('/save')
def save_image():
	thermcam.save_image()
	return ("Snapshot Saved")

@app.route('/inc_min_temp')
def inc_min_temp():
    thermcam.change_min_temp()
    return ("Increased Min Temp")

@app.route('/dec_min_temp')
def dec_min_temp():
    thermcam.change_min_temp(increase=False)
    return ("Decreased Min Temp")

@app.route('/inc_max_temp')
def inc_max_temp():
    thermcam.change_max_temp()
    return ("Increased Max Temp")

@app.route('/dec_max_temp')
def dec_max_temp():
    thermcam.change_max_temp(increase=False)
    return ("Decreased Max Temp")

@app.route('/units')
def change_units():
	thermcam.use_f = not thermcam.use_f
	return ("Units changed")

@app.route('/colormap')
def increment_colormap():
	thermcam.change_colormap()
	return ("Colormap changed")

@app.route('/colormapback')
def decrement_colormap():
	thermcam.change_colormap(forward=False)
	return ("Colormap changed back")

@app.route('/filter')
def toggle_filter():
	thermcam.filter_image=not thermcam.filter_image
	return ("Filtering Toggled")

@app.route('/interpolation')
def increment_interpolation():
	thermcam.change_interpolation()
	return ("Interpolation Changed")

@app.route('/interpolationback')
def decrement_interpolation():
	thermcam.change_interpolation(forward=False)
	return ("Interpolation Changed Back")

@app.route('/exit')
def appexit():
	global thermcam
	func = request.environ.get('werkzeug.server.shutdown')
	if func is None:
		raise RuntimeError('Not running with the Werkzeug Server')
	func()
	thermcam = None
	return 'Server shutting down...'

@app.route("/video_feed")
def video_feed():
	# return the response generated along with the specific media
	# type (mime type)
	return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

def setup_screen():
    # just assume square for now..    
    display_type = "square"
    
    # Create ST7789 LCD display class.
    if display_type in ("square", "rect", "round"):
        disp = ST7789.ST7789(
            height=135 if display_type == "rect" else 240,
            rotation=0 if display_type == "rect" else 90,
            port=0,
            cs=ST7789.BG_SPI_CS_FRONT,  # BG_SPI_CS_BACK or BG_SPI_CS_FRONT
            dc=9,
            backlight=19,               # 18 for back BG slot, 19 for front BG slot.
            spi_speed_hz=80 * 1000 * 1000,
            offset_left=0 if display_type == "square" else 40,
            offset_top=53 if display_type == "rect" else 0
        )

    elif display_type == "dhmini":
        disp = ST7789.ST7789(
            height=240,
            width=320,
            rotation=180,
            port=0,
            cs=1,
            dc=9,
            backlight=13,
            spi_speed_hz=60 * 1000 * 1000,
            offset_left=0,
            offset_top=0
       )

    else:
        print ("Invalid display type!")

    if disp == None:
        return
    
    # Initialize display.
    disp.begin()
    
    img = Image.new('RGB', (disp.width, disp.height), color=(0, 0, 0))

    draw = ImageDraw.Draw(img)

    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)

    display_msg = "Thermal Cam\nWeb server\n\n" + get_ip_address() + ":8000"
    size_x, size_y = draw.textsize(display_msg, font)

    text_x = disp.width
    text_y = (disp.height - size_y) // 2
    
    draw.rectangle((0, 0, disp.width, disp.height), (0, 0, 0))
    draw.text((0, 0), display_msg, font=font, fill=(255, 255, 255))
    disp.display(img)
    
def get_ip_address():
	"""Find the current IP address of the device"""
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	s.connect(("8.8.8.8", 80))
	ip_address=s.getsockname()[0]
	s.close()
	return ip_address

def pull_images():
	global thermcam, outputFrame
	# loop over frames from the video stream
	while thermcam is not None:
		current_frame=None
		try:
			current_frame = thermcam.update_image_frame()
		except Exception:
			print("Too many retries error caught; continuing...")
			logger.info(traceback.format_exc())

		# If we have a frame, acquire the lock, set the output frame, and release the lock
		if current_frame is not None:
			with lock:
				outputFrame = current_frame.copy()

def generate():
	# grab global references to the output frame and lock variables
	global outputFrame, lock
	# loop over frames from the output stream
	while True:
		# wait until the lock is acquired
		with lock:
			# check if the output frame is available, otherwise skip the iteration of the loop
			if outputFrame is None:
				continue
			# encode the frame in JPEG format
			(flag, encodedImage) = cv2.imencode(".jpg", outputFrame)
			# ensure the frame was successfully encoded
			if not flag:
				continue
		# yield the output frame in the byte format
		yield(b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encodedImage) + b'\r\n')

def start_server(output_folder:str = '/home/pi/pithermalcam/saved_snapshots/'):
	global thermcam
	# initialize the video stream and allow the camera sensor to warmup
	thermcam = pithermalcam(output_folder=output_folder)
	time.sleep(0.1)

	# start a thread that will perform motion detection
	t = threading.Thread(target=pull_images)
	t.daemon = True
	t.start()

	ip=get_ip_address()
	port=8000

	print(f'Server can be found at {ip}:{port}')

	# start the flask app
	app.run(host=ip, port=port, debug=False,threaded=True, use_reloader=False)


# If this is the main thread, simply start the server
if __name__ == '__main__':
    setup_screen()
    start_server()
	
