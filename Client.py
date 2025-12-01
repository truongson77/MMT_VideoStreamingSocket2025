from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
from collections import deque

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

BUFFER_SIZE = 30       # Tổng cache store
MIN_BUFFER = 10        # Số frame tối thiểu trước khi play cache
# ================================


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
        self.frameNbr = 0

        # Fragment buffer (original logic)
        self.frameBuffer = bytearray()

        self.frameCache = deque(maxlen=BUFFER_SIZE)
        self.buffering = False

    def createWidgets(self):
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        if self.state == self.READY:
            print("[CLIENT] PLAY clicked — start buffering stage")

            # Begin listening RTP immediately
            threading.Thread(target=self.listenRtp).start()

            # PLAY request
            self.playEvent = threading.Event()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)

            # ENABLE caching
            self.buffering = True

            # Wait until cache filled
            threading.Thread(target=self.waitForBuffer, daemon=True).start()

    def waitForBuffer(self):
        #print("[CACHE] Waiting for buffering...")

        # FIX RACE CONDITION: wait for FIRST frame
        while len(self.frameCache) == 0:
            if self.state != self.PLAYING:
                return

        # Now wait until MIN_BUFFER reached
        while len(self.frameCache) < MIN_BUFFER and self.state == self.PLAYING:
            pass

        #print(f"[CACHE] Buffer READY ({len(self.frameCache)} frames) → Start cached playback")

        # Begin cached playback
        threading.Thread(target=self.playFromCache, daemon=True).start()

    def playFromCache(self):
        cacheFile = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT

        while len(self.frameCache) > 0 and self.state == self.PLAYING:
            frameData = self.frameCache.popleft()

            with open(cacheFile, "wb") as f:
                f.write(frameData)

            #print(f"[CACHE] Playing cached frame — {len(self.frameCache)} left")

            try:
                photo = ImageTk.PhotoImage(Image.open(cacheFile))
                self.label.configure(image=photo)
                self.label.image = photo
            except:
                pass

    def listenRtp(self):
        while True:
            try:
                data = self.rtpSocket.recv(65536)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)

                    seq = rtpPacket.seqNum()
                    marker = rtpPacket.getMarker()
                    payload = rtpPacket.getPayload()

                    print(f"Current Seq Num: {seq}, marker={marker}")
                    
                    self.frameBuffer.extend(payload)

                    if marker == 1:
                        imageFile = self.writeFrame(self.frameBuffer)

                        # cache only during buffering phase
                        if self.buffering and len(self.frameCache) < BUFFER_SIZE:
                            with open(imageFile, "rb") as f:
                                self.frameCache.append(f.read())
                                print(f"[CACHE] Stored frame — {len(self.frameCache)} cached")

                        # Live update (used after cache fully consumed)
                        self.updateMovie(imageFile)

                        self.frameBuffer = bytearray()

            except:
                if hasattr(self, "playEvent") and self.playEvent.isSet():
                    break

                if self.teardownAcked == 1:
                    self.rtpSocket.close()
                    break

    def writeFrame(self, data):
        filepath = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        with open(filepath, "wb") as f:
            f.write(data)
        return filepath

    def updateMovie(self, imageFile):
        photo = ImageTk.PhotoImage(Image.open(imageFile))
        self.label.configure(image=photo, height=288)
        self.label.image = photo

    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.rtspSocket.connect((self.serverAddr, self.serverPort))

    def sendRtspRequest(self, requestCode):
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            self.rtspSeq += 1
            request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port= {self.rtpPort}"
            self.requestSent = self.SETUP

        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PLAY

        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PAUSE

        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.TEARDOWN

        else:
            return

        self.rtspSocket.send(request.encode())
        print("\nData sent:\n" + request)

    def recvRtspReply(self):
        while True:
            reply = self.rtspSocket.recv(1024)
            if reply:
                self.parseRtspReply(reply.decode("utf-8"))

            if self.requestSent == self.TEARDOWN:
                self.rtspSocket.close()
                break

    def parseRtspReply(self, data):
        lines = data.split('\n')
        seqNum = int(lines[1].split(' ')[1])

        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])

            if self.sessionId == 0:
                self.sessionId = session

            if self.sessionId == session:
                if int(lines[0].split(' ')[1]) == 200:

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

    def openRtpPort(self):
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        self.rtpSocket.bind(("", self.rtpPort))

    def handler(self):
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()
