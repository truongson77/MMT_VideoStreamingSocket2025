# -*- coding: utf-8 -*-
from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import time
import queue

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"


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
        
        self.savedFrameCount = 0
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
        self.rtpSocket = None
        
        # ===== SIMPLE: Dùng queue.Queue thay vì deque =====
        self.frameBuffer = queue.Queue(maxsize=100)
        self.MIN_BUFFER = 20
        self.is_buffering = True
        
        # Frame assembly
        self.currentFrameData = b''
        self.currentSeqNum = -1
        
        # Stats
        self.expectedSeq = None
        self.receivedPackets = 0
        self.lostPackets = 0
        self.receivedFrames = 0
        self.totalBytesReceived = 0
        self.firstPacketTime = None
        self.lastPacketTime = None
        self.lastFrameTime = None
        self.frameIntervals = []
        
        # FPS tracking
        self.fpsFrameCount = 0
        self.fpsStartTime = time.time()
        self.displayFPS = 0.0

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
        
        # Stats label
        self.statsLabel = Label(
            self.master, 
            text="State: INIT", 
            font=("Consolas", 10), 
            anchor=W, 
            justify=LEFT
        )
        self.statsLabel.grid(row=2, column=0, columnspan=4, sticky=W+E, padx=5, pady=2)

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        if self.state == self.PLAYING or self.receivedPackets > 0:
            self.print_stats()
        
        self.sendRtspRequest(self.TEARDOWN)
        time.sleep(0.1)
        self.master.destroy()

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)
            time.sleep(0.1)
            self.print_stats()

    def playMovie(self):
        if self.state == self.READY:
            print("[CLIENT] PLAY clicked")
            
            # ===== SIMPLE: Chỉ start RTP listener 1 lần =====
            if not hasattr(self, 'rtpListenerStarted'):
                threading.Thread(target=self.listenRtp, daemon=True).start()
                self.rtpListenerStarted = True
            
            self.sendRtspRequest(self.PLAY)
            self.fpsStartTime = time.time()
            self.fpsFrameCount = 0

    # ===== KEY: Dùng Tkinter after() thay vì thread riêng =====
    def consumeBuffer(self):
        """Được gọi bởi Tkinter event loop - KHÔNG dùng thread riêng"""
        
        # Dừng nếu không PLAYING
        if self.state != self.PLAYING:
            return
        
        if self.requestSent == self.PAUSE:
            return
        
        currentSize = self.frameBuffer.qsize()
        now = time.time()
        timeDiff = now - self.fpsStartTime
        
        # Update FPS display mỗi giây
        if timeDiff >= 1.0:
            self.displayFPS = self.fpsFrameCount / timeDiff
            self.fpsFrameCount = 0
            self.fpsStartTime = now
        
        # Calculate loss rate
        lossRate = 0.0
        totalExpected = self.receivedPackets + self.lostPackets
        if totalExpected > 0:
            lossRate = (self.lostPackets / totalExpected) * 100
        
        # ===== BUFFERING LOGIC =====
        if self.is_buffering:
            if currentSize < self.MIN_BUFFER:
                percent = int((currentSize / self.MIN_BUFFER) * 100)
                self.statsLabel.config(text=f"Buffering... {percent}% | Loss: {lossRate:.1f}%")
                self.master.after(50, self.consumeBuffer)
                return
            else:
                self.is_buffering = False
        
        # Buffer underrun
        if currentSize == 0:
            self.is_buffering = True
            self.master.after(20, self.consumeBuffer)
            return
        
        # ===== DISPLAY FRAME =====
        if not self.frameBuffer.empty():
            imageFile = self.frameBuffer.get()
            self.updateMovie(imageFile)
            self.fpsFrameCount += 1
            
            try:
                os.remove(imageFile)
            except:
                pass
            
            # Update stats display
            statText = f"FPS: {self.displayFPS:.1f} | Loss: {lossRate:.2f}% | Buffer: {currentSize}"
            self.statsLabel.config(text=statText)
        
        # ===== ADAPTIVE DELAY =====
        delay = 50  # Default 20 fps
        if currentSize > 50:
            delay = 40  # Speed up if buffer high
        
        # ===== KEY: Schedule next frame với Tkinter =====
        self.master.after(delay, self.consumeBuffer)

    def listenRtp(self):
        """Thread duy nhất cho RTP - đơn giản và ổn định"""
        print("[CLIENT] RTP listener started")
        
        while True:
            try:
                if self.teardownAcked == 1:
                    break
                
                data = self.rtpSocket.recv(40960)
                
                if data:
                    now = time.time()
                    self.totalBytesReceived += len(data)
                    
                    if self.firstPacketTime is None:
                        self.firstPacketTime = now
                    self.lastPacketTime = now
                    
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    receivedSeqNum = rtpPacket.seqNum()
                    marker = rtpPacket.getMarker()
                    payload = rtpPacket.getPayload()
                    
                    self.receivedPackets += 1
                    
                    # ===== Log seq number mỗi 1000 packets =====
                    if receivedSeqNum % 2000 == 0:
                        print(f"[RTP] Seq={receivedSeqNum}, marker={marker}")
                    if marker == 1 and (self.receivedFrames % 20 == 0):
                        print(f"[RTP] EndFrame at seq={receivedSeqNum} (marker=1)")
                    # Packet loss detection
                    if self.currentSeqNum != -1:
                        diff = receivedSeqNum - self.currentSeqNum
                        if diff > 1:
                            self.lostPackets += (diff - 1)
                            self.currentFrameData = b''  # Reset frame on loss
                    
                    self.currentSeqNum = receivedSeqNum
                    self.currentFrameData += payload
                    
                    # Frame complete
                    if marker == 1:
                        if len(self.currentFrameData) > 0:
                            self.receivedFrames += 1
                            
                            # Track frame intervals for jitter
                            frameTime = now
                            if not self.is_buffering:
                                if self.lastFrameTime is not None:
                                    interval = frameTime - self.lastFrameTime
                                    self.frameIntervals.append(interval)
                                self.lastFrameTime = frameTime
                            else:
                                self.lastFrameTime = None
                            
                            # Write frame
                            path = self.writeFrame(self.currentFrameData, receivedSeqNum)
                            
                            # Add to buffer
                            if not self.frameBuffer.full():
                                self.frameBuffer.put(path)
                        
                        self.currentFrameData = b''
            
            except socket.timeout:
                continue
            except Exception as e:
                if self.teardownAcked == 1:
                    break
                print(f"[RTP ERROR] {e}")
        
        print("[CLIENT] RTP listener stopped")

    def writeFrame(self, data, frameNum):
        cacheName = CACHE_FILE_NAME + str(self.sessionId) + "_" + str(frameNum) + CACHE_FILE_EXT
        
        try:
            with open(cacheName, "wb") as f:
                f.write(data)
            
            # Save some frames for comparison
            if self.savedFrameCount < self.MAX_SAVE_FRAMES:
                comparePath = f"streamed_frames/frame_{self.receivedFrames:04d}.jpg"
                os.makedirs("streamed_frames", exist_ok=True)
                with open(comparePath, "wb") as f:
                    f.write(data)
                self.savedFrameCount += 1
        except:
            pass
        
        return cacheName

    def updateMovie(self, imageFile):
        try:
            image = Image.open(imageFile)
            photo = ImageTk.PhotoImage(image)
            self.label.configure(image=photo, height=288)
            self.label.image = photo
        except:
            pass

    def connectToServer(self):
        try:
            self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
            print(f"[CLIENT] Connected to {self.serverAddr}:{self.serverPort}")
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")
            sys.exit(1)

    def sendRtspRequest(self, requestCode):
        request = ""
        
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq += 1
            request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port={self.rtpPort}\n"
            self.requestSent = self.SETUP
        
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}\n"
            self.requestSent = self.PLAY
        
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}\n"
            self.requestSent = self.PAUSE
        
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}\n"
            self.requestSent = self.TEARDOWN
        
        else:
            return
        
        try:
            self.rtspSocket.send(request.encode())
            print(f"\n[CLIENT] Sent:\n{request}")
        except Exception as e:
            print(f"[ERROR] Send failed: {e}")

    def recvRtspReply(self):
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply:
                    decodedReply = reply.decode("utf-8")
                    print(f"[CLIENT] Received:\n{decodedReply}")
                    self.parseRtspReply(decodedReply)
                
                if self.requestSent == self.TEARDOWN:
                    self.rtspSocket.shutdown(socket.SHUT_RDWR)
                    self.rtspSocket.close()
                    break
            except:
                break

    def parseRtspReply(self, data):
        try:
            lines = data.split('\n')
            if len(lines) < 3:
                return
            
            statusCode = int(lines[0].split(' ')[1])
            seqNum = int(lines[1].split(' ')[1])
            session = int(lines[2].split(' ')[1])
            
            if seqNum == self.rtspSeq and statusCode == 200:
                if self.sessionId == 0:
                    self.sessionId = session
                
                if self.sessionId == session:
                    if self.requestSent == self.SETUP:
                        self.state = self.READY
                        self.openRtpPort()
                        print("[CLIENT] State -> READY")
                    
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                        self.currentFrameData = b''
                        self.currentSeqNum = -1
                        self.is_buffering = True
                        # ===== KEY: Start consuming với Tkinter =====
                        self.lastFrameTime = None
                        self.frameIntervals.clear()
                        self.consumeBuffer()
                        print("[CLIENT] State -> PLAYING")
                    
                    elif self.requestSent == self.PAUSE:
                        self.lastFrameTime = None
                        self.state = self.READY
                        print("[CLIENT] State -> READY")
                    
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        self.teardownAcked = 1
                        print("[CLIENT] State -> INIT")
        except Exception as e:
            print(f"[ERROR] Parse reply failed: {e}")

    def openRtpPort(self):
        try:
            self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*1024)
            self.rtpSocket.settimeout(0.5)
            self.rtpSocket.bind(('', self.rtpPort))
            print(f"[CLIENT] RTP socket on port {self.rtpPort}")
        except Exception as e:
            print(f"[ERROR] Failed to open RTP port: {e}")
            sys.exit(1)

    def handler(self):
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()

    def print_stats(self):
        print("\n========== CLIENT STATS ==========")
        
        totalPackets = self.receivedPackets + self.lostPackets
        lossRate = (self.lostPackets * 100.0 / totalPackets) if totalPackets > 0 else 0.0
        
        print(f"Received packets     : {self.receivedPackets}")
        print(f"Lost packets         : {self.lostPackets}")
        print(f"Loss rate            : {lossRate:.2f}%")
        
        if self.firstPacketTime and self.lastPacketTime:
            duration = max(self.lastPacketTime - self.firstPacketTime, 1e-6)
            throughput = (self.totalBytesReceived * 8) / duration / 1_000_000
        else:
            duration = 0
            throughput = 0
        
        print(f"Duration             : {duration:.2f}s")
        print(f"Throughput           : {throughput:.2f} Mbps")
        print(f"Total bytes received : {self.totalBytesReceived}")
        print(f"Received frames      : {self.receivedFrames}")
        
        fps = (self.receivedFrames / duration) if duration > 0 else 0
        print(f"Playback FPS         : {fps:.2f}")
        
        if len(self.frameIntervals) > 1:
            mean = sum(self.frameIntervals) / len(self.frameIntervals)
            variance = sum((x - mean) ** 2 for x in self.frameIntervals) / len(self.frameIntervals)
            jitter_ms = (variance ** 0.5) * 1000
        else:
            jitter_ms = 0
        
        print(f"Jitter (frame)       : {jitter_ms:.2f} ms")
        print("=================================\n")