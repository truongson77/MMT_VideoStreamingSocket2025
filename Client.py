from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
from collections import deque
import time

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

# Buffer cho HD video
BUFFER_SIZE = 90   # tổng số frame có thể cache (~3s nếu ~30fps)
MIN_BUFFER = 30    # số frame phải có trước khi bắt đầu play (~1s)


class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        
        self.savedFrameCount = 0  # ← THÊM DÒNG NÀY
        self.MAX_SAVE_FRAMES = 5 
        
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()

        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename

        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0

        self.connectToServer()

        # ==== RTP / FRAME STATS ====
        self.expectedSeq = None
        self.receivedPackets = 0
        self.lostPackets = 0

        self.receivedFrames = 0
        self.totalBytesReceived = 0

        self.firstPacketTime = None
        self.lastPacketTime = None

        self.lastFrameTime = None
        self.frameIntervals = []   # dùng để tính jitter

        # Buffer + cache
        self.frameBuffer = bytearray()
        self.frameCache = deque(maxlen=BUFFER_SIZE)
        self.buffering = False

    # -----------------------------------------------------
    # GUI SETUP
    # -----------------------------------------------------
    def createWidgets(self):
        self.setup = Button(
            self.master, width=20, padx=3, pady=3,
            text="Setup", command=self.setupMovie
        )
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        self.start = Button(
            self.master, width=20, padx=3, pady=3,
            text="Play", command=self.playMovie
        )
        self.start.grid(row=1, column=1, padx=2, pady=2)

        self.pause = Button(
            self.master, width=20, padx=3, pady=3,
            text="Pause", command=self.pauseMovie
        )
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        self.teardown = Button(
            self.master, width=20, padx=3, pady=3,
            text="Teardown", command=self.exitClient
        )
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        self.label = Label(self.master, height=19)
        self.label.grid(
            row=0, column=0, columnspan=4,
            sticky=W+E+N+S, padx=5, pady=5
        )

    # -----------------------------------------------------

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)
            self.print_stats()  

    def playMovie(self):
        if self.state == self.READY:
            print("[CLIENT] PLAY clicked — buffering...")

            # bắt đầu thread nhận RTP
            threading.Thread(target=self.listenRtp).start()

            self.playEvent = threading.Event()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)

            self.buffering = True

            # thread đợi cho đủ MIN_BUFFER frame rồi mới play cache
            threading.Thread(target=self.waitForBuffer, daemon=True).start()

    # -----------------------------------------------------
    # Buffer logic
    # -----------------------------------------------------

    def waitForBuffer(self):
        # chờ frame đầu tiên
        while len(self.frameCache) == 0:
            if self.state != self.PLAYING:
                return

        # đợi tới khi đủ MIN_BUFFER frame
        while len(self.frameCache) < MIN_BUFFER and self.state == self.PLAYING:
            pass

        # bắt đầu play từ cache
        threading.Thread(target=self.playFromCache, daemon=True).start()

    def playFromCache(self):
        cacheFile = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT

        while len(self.frameCache) > 0 and self.state == self.PLAYING:
            frameData = self.frameCache.popleft()

            with open(cacheFile, "wb") as f:
                f.write(frameData)

            try:
                photo = ImageTk.PhotoImage(Image.open(cacheFile))
                self.label.configure(image=photo)
                self.label.image = photo
            except Exception:
                pass

    # -----------------------------------------------------
    # RTP RECEIVING + FRAME ASSEMBLY
    # -----------------------------------------------------

    def listenRtp(self):
        while True:
            try:
                data = self.rtpSocket.recv(65536)
                if not data:
                    continue

                # ---- Thống kê byte + thời gian ----
                now = time.time()
                packet_len = len(data)
                self.totalBytesReceived += packet_len

                if self.firstPacketTime is None:
                    self.firstPacketTime = now
                self.lastPacketTime = now

                # ====================================

                rtpPacket = RtpPacket()
                rtpPacket.decode(data)

                seq = rtpPacket.seqNum()
                marker = rtpPacket.getMarker()
                payload = rtpPacket.getPayload()

                # In seq number cho debug
                print(f"[RTP] Seq={seq}, marker={marker}, len={len(payload)} bytes")

                # ---- Packet loss detection ----
                if self.expectedSeq is None:
                    self.expectedSeq = seq + 1
                else:
                    if seq > self.expectedSeq:
                        self.lostPackets += (seq - self.expectedSeq)
                    self.expectedSeq = seq + 1

                self.receivedPackets += 1

                # ---- Ghép fragment ----
                self.frameBuffer.extend(payload)

                if marker == 1:
                    # Frame hoàn chỉnh
                    self.receivedFrames += 1

                    frame_time = now
                    if self.lastFrameTime is not None:
                        interval = frame_time - self.lastFrameTime
                        self.frameIntervals.append(interval)
                    self.lastFrameTime = frame_time

                    imageFile = self.writeFrame(self.frameBuffer)

                    # CACHE
                    if self.buffering and len(self.frameCache) < BUFFER_SIZE:
                        with open(imageFile, "rb") as f:
                            self.frameCache.append(f.read())
                            print(f"[CACHE] Stored frame — {len(self.frameCache)}")

                    # LIVE UPDATE (khi cache tiêu hết)
                    self.updateMovie(imageFile)
                    self.frameBuffer = bytearray()

            except Exception:
                if hasattr(self, "playEvent") and self.playEvent.isSet():
                    break

                if self.teardownAcked == 1:
                    self.print_stats()
                    try:
                        self.rtpSocket.close()
                    except Exception:
                        pass
                    break

    # -----------------------------------------------------

    def writeFrame(self, data):
        filepath = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        with open(filepath, "wb") as f:
            f.write(data)
                # ===== THÊM: Lưu thêm một số frame để so sánh =====
        if self.savedFrameCount < self.MAX_SAVE_FRAMES:
            frame_num = self.receivedFrames
            compare_path = f"streamed_frames/frame_{frame_num:04d}.jpg"
            
            # Tạo thư mục nếu chưa có
            os.makedirs("streamed_frames", exist_ok=True)
            
            with open(compare_path, "wb") as f:
                f.write(data)
            
            self.savedFrameCount += 1
            print(f"[SAVE] Saved for comparison: {compare_path}")
        # ==================================================
        
        return filepath
    

    def updateMovie(self, imageFile):
        photo = ImageTk.PhotoImage(Image.open(imageFile))
        self.label.configure(image=photo, height=288)
        self.label.image = photo

    # -----------------------------------------------------
    # RTSP  
    # -----------------------------------------------------

    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.rtspSocket.connect((self.serverAddr, self.serverPort))

    def sendRtspRequest(self, requestCode):
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            self.rtspSeq += 1
            request = (
                f"SETUP {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Transport: RTP/UDP; client_port= {self.rtpPort}"
            )
            self.requestSent = self.SETUP

        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = (
                f"PLAY {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Session: {self.sessionId}"
            )
            self.requestSent = self.PLAY

        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = (
                f"PAUSE {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Session: {self.sessionId}"
            )
            self.requestSent = self.PAUSE

        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = (
                f"TEARDOWN {self.fileName} RTSP/1.0\n"
                f"CSeq: {self.rtspSeq}\n"
                f"Session: {self.sessionId}"
            )
            self.requestSent = self.TEARDOWN

        else:
            return

        self.rtspSocket.send(request.encode())
        print("\nData sent:\n" + request)

    # -----------------------------------------------------

    def recvRtspReply(self):
        while True:
            reply = self.rtspSocket.recv(1024)
            if reply:
                self.parseRtspReply(reply.decode("utf-8"))

            if self.requestSent == self.TEARDOWN:
                self.rtspSocket.close()
                break

    # -----------------------------------------------------

    def parseRtspReply(self, data):
        lines = data.split('\n')
        seqNum = int(lines[1].split(' ')[1])

        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])

            if self.sessionId == 0:
                self.sessionId = session

            if self.sessionId == session and int(lines[0].split(' ')[1]) == 200:

                if self.requestSent == self.SETUP:
                    self.state = self.READY
                    self.openRtpPort()

                elif self.requestSent == self.PLAY:
                    self.state = self.PLAYING

                elif self.requestSent == self.PAUSE:
                    self.state = self.READY
                    self.playEvent.set()

                elif self.requestSent == self.TEARDOWN:
                    self.state = self.INIT
                    self.teardownAcked = 1

    # -----------------------------------------------------

    def openRtpPort(self):
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        self.rtpSocket.bind(("", self.rtpPort))

    # -----------------------------------------------------

    def handler(self):
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()

    # -----------------------------------------------------
    # FINAL CLIENT STATISTICS
    # -----------------------------------------------------

    def print_stats(self):
        print("\n========== CLIENT STATS ==========")

        totalPackets = self.receivedPackets + self.lostPackets

        # Packet loss rate
        if totalPackets > 0:
            lossRate = self.lostPackets * 100.0 / totalPackets
        else:
            lossRate = 0.0

        print(f"Received packets     : {self.receivedPackets}")
        print(f"Lost packets         : {self.lostPackets}")
        print(f"Loss rate            : {lossRate:.2f}%")

        # Throughput
        if self.firstPacketTime and self.lastPacketTime:
            duration = max(self.lastPacketTime - self.firstPacketTime, 1e-6)
            throughput = (self.totalBytesReceived * 8) / duration / 1_000_000
        else:
            duration = 0
            throughput = 0

        print(f"Duration             : {duration:.2f}s")
        print(f"Throughput           : {throughput:.2f} Mbps")
        print(f"Total bytes received : {self.totalBytesReceived}")

        # Frames received
        print(f"Received frames      : {self.receivedFrames}")

        # FPS thực tế
        if duration > 0:
            fps = self.receivedFrames / duration
        else:
            fps = 0
        print(f"Playback FPS         : {fps:.2f}")

        # Jitter (dựa trên khoảng cách giữa các frame hoàn chỉnh)
        if len(self.frameIntervals) > 1:
            mean = sum(self.frameIntervals) / len(self.frameIntervals)
            variance = sum((x - mean) ** 2 for x in self.frameIntervals) / len(self.frameIntervals)
            jitter_ms = (variance ** 0.5) * 1000
        else:
            jitter_ms = 0

        print(f"Jitter (frame)       : {jitter_ms:.2f} ms")
        print("=================================\n")