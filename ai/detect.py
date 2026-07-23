import cv2

def detect_plate(image_path):
    image = cv2.imread(image_path)

    if image is None:
        print("Could not read image.")
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    gray = cv2.bilateralFilter(gray, 11, 17, 17)

    edged = cv2.Canny(gray, 30, 200)

    contours, _ = cv2.findContours(
        edged,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE
    )

    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:20]

    plate = None

    for contour in contours:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)

        if len(approx) == 4:
            x, y, w, h = cv2.boundingRect(approx)

            # Ignore tiny detections
            if w > 50 and h > 20:
                plate = image[y:y+h, x:x+w]
                break

    if plate is None:
        print("No plate found.")
        return None

    cv2.imwrite("plate.jpg", plate)

    print("Plate saved as plate.jpg")

    return plate