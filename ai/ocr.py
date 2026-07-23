from fast_plate_ocr import LicensePlateRecognizer

model = LicensePlateRecognizer("cct-s-v2-global-model")

result = model.run("plate.jpg")

print(result)