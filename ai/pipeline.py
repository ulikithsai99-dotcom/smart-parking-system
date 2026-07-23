from detect import detect_plate
import subprocess

plate = detect_plate("test_images/test_image.jpg")

if plate is not None:
    subprocess.run(["python", "ocr.py"])