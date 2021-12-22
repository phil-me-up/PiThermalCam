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
import os
import io

import colorsys
import ioexpander

from trackball import TrackBall

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

import ST7789

I2C_ADDR = 0x0F  # 0x18 for IO Expander, 0x0F for the encoder breakout

PIN_RED = 1
PIN_GREEN = 7
PIN_BLUE = 2

POT_ENC_A = 12
POT_ENC_B = 3
POT_ENC_C = 11

BRIGHTNESS = 0.5                # Effectively the maximum fraction of the period that the LED will be on
PERIOD = int(255 / BRIGHTNESS)  # Add a period large enough to get 0-255 steps at the desired brightness

# Setup Rotary Input
ioe = None
rotary_count = 0
rotary_start_offset = -1

# Set up Trackball Breakout.
use_trackball = True
trackball = TrackBall(interrupt_pin=4)
trackball_delta_x = 0
trackball_delta_y = 0
trackball_state_x = 0
trackball_state_y = 0
trackball_clicks = 0
trackball_msg = None

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

def setup_rotary_input():
    global ioe, rotary_count, rotary_start_offset
    ioe = ioexpander.IOE(i2c_addr=I2C_ADDR, interrupt_pin=4)

    # Swap the interrupt pin for the Rotary Encoder breakout
    if I2C_ADDR == 0x0F:
        ioe.enable_interrupt_out(pin_swap=True)

    ioe.setup_rotary_encoder(1, POT_ENC_A, POT_ENC_B, pin_c=POT_ENC_C)

    ioe.set_pwm_period(PERIOD)
    ioe.set_pwm_control(divider=2)  # PWM as fast as we can to avoid LED flicker

    ioe.set_mode(PIN_RED, ioexpander.PWM, invert=True)
    ioe.set_mode(PIN_GREEN, ioexpander.PWM, invert=True)
    ioe.set_mode(PIN_BLUE, ioexpander.PWM, invert=True)

    print("Running LED with {} brightness steps.".format(int(PERIOD * BRIGHTNESS)))

    rotaty_count = 0
    rotary_start_offset = ioe.read_rotary_encoder(1)


def setup_trackball_input():
    global use_trackball, trackball, trackball_delta_x, trackball_delta_y, trackball_state_x, trackball_state_y, trackball_clicks, trackball_msg

    if use_trackball is False:
        return

    trackball.set_rgbw(255, 0, 0, 0)
    up, down, left, right, switch, state = trackball.read()

    trackball_delta_x += right
    trackball_delta_x -= left
    trackball_delta_y += up
    trackball_delta_y -= down

    trackball_state_x = 0
    trackball_state_y = 0

    trackball_clicks = 0
    trackball_msg = None


def update_rotary_input():
    global ioe, rotary_count, rotary_start_offset

    if ioe.get_interrupt():
        rotary_count = ioe.read_rotary_encoder(1)
        rotary_count = rotary_count - rotary_start_offset
        setup_trackball_input()  # Call this just to reset all the trackball input when screens change
        ioe.clear_interrupt()

    h = (rotary_count % 360) / 360.0

    rotary_r, rotary_g, rotary_b = [int(c * PERIOD * BRIGHTNESS) for c in colorsys.hsv_to_rgb(h, 1.0, 1.0)]
    ioe.output(PIN_RED, rotary_r)
    ioe.output(PIN_GREEN, rotary_g)
    ioe.output(PIN_BLUE, rotary_b)

    # print(rotary_count, rotary_r, rotary_g, rotary_b)

    # time.sleep(1.0 / 30)

def update_trackball():
    global use_trackball, trackball, trackball_delta_x, trackball_delta_y, trackball_clicks, trackball_state_x, trackball_state_y, trackball_msg

    if use_trackball is False:
        return

    up, down, left, right, switch, state = trackball.read()

    print("r: {:02d} u: {:02d} d: {:02d} l: {:02d} switch: {:03d} state: {}".format(right, up, down, left, switch, state))

    x = right
    y = up

    if down > 1:
        trackball_state_y += 1
        trackball_state_x = 0
    elif up > 1:
        trackball_state_y -= 1
        trackball_state_x = 0
    elif right > 1:
        trackball_state_x += 1
    elif left > 1:
        trackball_state_x -= 1

    if state:
        trackball_clicks += 1
        trackball_state_x = 0
        trackball_state_y = 0

    print('Trackball X:' + str(x) + ' Y:' + str(y) + ' State X:' + str(trackball_state_x) + ' Y:' + str(trackball_state_y))

    if trackball_state_y == 1:
        if trackball_state_x > 1:
            increment_colormap()
            trackball_msg = "Colour Map Inc"
            trackball_state_x = 0
        elif trackball_state_x < -1:
            decrement_colormap()
            trackball_msg = "Colour Map Dec"
            trackball_state_x = 0
        trackball_delta_x = 0

def update_input():
    while True:
        update_rotary_input()
        update_trackball()
        #time.sleep(0.005)

def setup_screen():
    global disp
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

def add_trackball_msg(draw=None):
    global disp, trackball_msg

    if trackball_msg is not None and draw is not None and disp is not None:
        print("blah")
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        half_rec_height = 20
        rec_loc = (disp.height / 2)
        draw.rectangle((0, (rec_loc - half_rec_height), disp.width, (rec_loc + half_rec_height)), (45, 45, 45))
        draw.text((0, rec_loc), trackball_msg, font=font, fill=(255, 255, 255))


def update_screen(current_frame=None):
    global disp, thermcam, rotary_count
    img = Image.new('RGB', (disp.width, disp.height), color=(0, 0, 0))

    #if thermcam is not None:
    #    img = Image.fromarray(thermcam.get_raw_image(disp.width, disp.height))

    draw = ImageDraw.Draw(img)

    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)

    print(rotary_count)
    if rotary_count == 0:
        # Get the network ID
        ssid=os.popen("sudo iwgetid -r").read()

        # print out
        display_msg = "Thermal Cam\nWeb server\n\nSSID: " + ssid + "\n" + get_ip_address() + ":8000"
        size_x, size_y = draw.textsize(display_msg, font)

        text_x = disp.width
        text_y = (disp.height - size_y) // 2

        draw.rectangle((0, 0, disp.width, disp.height), (0, 0, 0))
        draw.text((0, 0), display_msg, font=font, fill=(255, 255, 255))

        add_trackball_msg(draw)
        disp.display(img)

    #image = Image.open(bytearray(lastImg))
    if rotary_count == 1:
        error_msg = None
        if current_frame is not None:
            try:
                (flag, encodedImage) = cv2.imencode(".jpg", current_frame)
                if flag:
                    image = Image.open(io.BytesIO(bytearray(encodedImage)))

                    # Resize the image
                    image = image.resize((disp.width, disp.height))

                    # Draw the image on the display hardware.
                    # print('Drawing image')

                    add_trackball_msg(draw)

                    disp.display(image)
            except:
                error_msg = "Failed to Draw Image"
        else:
            error_msg = "Can't Get Current Frame"

        if error_msg is not None:
            size_x, size_y = draw.textsize(error_msg, font)

            text_x = disp.width
            text_y = (disp.height - size_y) // 2

            draw.rectangle((0, 0, disp.width, disp.height), (0, 0, 0))
            draw.text((0, 0), error_msg, font=font, fill=(255, 255, 255))
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

        #update_rotary_input()
        #update_trackball()
        update_screen(current_frame)

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

	t2 = threading.Thread(target=update_input)
	t2.daemon = True
	t2.start()

	ip=get_ip_address()
	port=8000

	print(f'Server can be found at {ip}:{port}')

	# start the flask app
	app.run(host=ip, port=port, debug=False,threaded=True, use_reloader=False)


# If this is the main thread, simply start the server
if __name__ == '__main__':
    setup_rotary_input()
    setup_trackball_input()
    setup_screen()
    #update_screen()
    start_server()

	