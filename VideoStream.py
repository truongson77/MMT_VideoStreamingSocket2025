import cv2


class VideoStream:
    def __init__(self, filename):
        """Mở file video bằng OpenCV."""
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
            # Hết video
            return None

        # Nén frame thành JPEG (giống file .Mjpeg cũ)
        success, jpeg = cv2.imencode(".jpg", frame)
        if not success:
            return None

        self.frameNum += 1
        return jpeg.tobytes()

    def frameNbr(self):
        """Trả về số thứ tự frame hiện tại."""
        return self.frameNum

    def __del__(self):
        """Giải phóng tài nguyên khi đối tượng bị hủy."""
        if hasattr(self, "cap") and self.cap.isOpened():
            self.cap.release()