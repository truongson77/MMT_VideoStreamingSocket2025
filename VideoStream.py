import cv2


class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        self.cap = cv2.VideoCapture(self.filename)

        if not self.cap.isOpened():
            raise IOError(f"Cannot open video file: {self.filename}")

        self.frameNum = 0

    def nextFrame(self):
        """
        Đọc frame tiếp theo từ video, nén JPEG, trả về bytes.
        Nếu hết video -> trả về None.
        """
        if not self.cap.isOpened():
            return None

        ret, frame = self.cap.read()
        if not ret:
            return None  # Hết video

        # JPEG quality cao cho HD
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 98]
        success, jpeg = cv2.imencode(".jpg", frame, encode_param)
        if not success:
            return None

        self.frameNum += 1
        return jpeg.tobytes()

    def frameNbr(self):
        return self.frameNum

    def __del__(self):
        if hasattr(self, "cap") and self.cap.isOpened():
            self.cap.release()