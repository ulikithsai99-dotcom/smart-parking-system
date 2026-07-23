from detect import PlateDetector

detector = PlateDetector()

results = detector.detect("test_images/test_image.jpg")

for result in results:
    print("Plates Found:", len(result.boxes))

    for box in result.boxes:
        print(box.xyxy)