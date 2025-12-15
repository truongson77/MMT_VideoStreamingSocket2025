# -*- coding: utf-8 -*-
from random import randint
import threading, socket, traceback
from time import time, sleep

from VideoStream import VideoStream
from RtpPacket import RtpPacket

MAX_PAYLOAD = 1400
TARGET_FPS = 20.0
FRAME_INTERVAL = 1.0 / TARGET_FPS


class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'

    INIT = 0
    READY = 1
    PLAYING = 2

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2

    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        self.state = self.INIT

        # RTP sequence number (per packet)
        self.rtpSeqNum = 0

        # Thread control
        self.stopEvent = threading.Event()
        self.pauseEvent = threading.Event()
        self.worker = None

        # Stats for "current play segment"
        self.resetStats()

    def resetStats(self):
        self.bytes_sent = 0
        self.packets_sent = 0
        self.frames_sent = 0
        self.totalFragments = 0
        self.maxFragmentsPerFrame = 0
        self.firstSendTime = None  # set when PLAY starts

    def run(self):
        threading.Thread(target=self.recvRtspRequest, daemon=True).start()

    def recvRtspRequest(self):
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:
            try:
                data = connSocket.recv(256)
                if data:
                    print("Data received:\n" + data.decode("utf-8"))
                    self.processRtspRequest(data.decode("utf-8"))
            except:
                break

    def processRtspRequest(self, data):
        request = data.split('\n')
        line1 = request[0].split(' ')
        requestType = line1[0]
        filename = line1[1]
        seq = request[1].split(' ')
        cseq = seq[1] if len(seq) > 1 else "0"

        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("processing SETUP\n")
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, cseq)
                    return

                self.clientInfo['session'] = randint(100000, 999999)

                # Parse RTP port safely
                try:
                    lineWithPort = [l for l in request if 'client_port' in l][0]
                    partAfterEq = lineWithPort.split('client_port=')[1]
                    portStr = partAfterEq.split('-')[0].split(';')[0].strip()
                    self.clientInfo['rtpPort'] = portStr
                    print(f"Client RTP Port: {self.clientInfo['rtpPort']}")
                except:
                    print("Error parsing RTP Port, defaulting to 25000")
                    self.clientInfo['rtpPort'] = "25000"

                # Create RTP socket once
                if 'rtpSocket' not in self.clientInfo:
                    self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

                self.replyRtsp(self.OK_200, cseq)

        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY\n")
                self.state = self.PLAYING

                # Reset stats for this play segment
                self.resetStats()
                self.firstSendTime = time()

                # Resume
                self.pauseEvent.clear()
                self.stopEvent.clear()

                self.replyRtsp(self.OK_200, cseq)

                # Start send thread only once
                if self.worker is None or not self.worker.is_alive():
                    self.worker = threading.Thread(target=self.sendRtp, daemon=True)
                    self.worker.start()
                    print("[SERVER] sendRtp thread started")
                else:
                    print("[SERVER] sendRtp thread already running (resume only)")

        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE\n")
                self.state = self.READY

                # Pause only (do NOT kill thread)
                self.pauseEvent.set()

                self.printServerStats()
                self.replyRtsp(self.OK_200, cseq)

        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN\n")

            # Stop thread
            self.stopEvent.set()
            self.pauseEvent.set()

            self.replyRtsp(self.OK_200, cseq)

            # Close RTP socket
            try:
                if 'rtpSocket' in self.clientInfo:
                    self.clientInfo['rtpSocket'].close()
            except:
                pass

    def sendRtp(self):
        print("[SERVER] sendRtp running - target 20 fps")

        while not self.stopEvent.is_set():

            # pause: wait until resumed
            if self.pauseEvent.is_set():
                sleep(0.01)
                continue

            # fixed pacing
            sleep(FRAME_INTERVAL)

            # read next frame
            data = self.clientInfo['videoStream'].nextFrame()
            if not data:
                print("[SERVER] End of video")
                break

            frameSize = len(data)
            frameNumber = self.clientInfo['videoStream'].frameNbr()

            if frameNumber % 50 == 0:
                print(f"[SERVER] Frame {frameNumber} - {frameSize} bytes")

            try:
                address = self.clientInfo['rtspSocket'][1][0]
                port = int(self.clientInfo['rtpPort'])
                rtpSocket = self.clientInfo['rtpSocket']

                fragmentsForThisFrame = 0
                offset = 0

                # IMPORTANT: do not check pause mid-frame; finish sending this frame
                while offset < frameSize:
                    chunk = data[offset: offset + MAX_PAYLOAD]
                    offset += MAX_PAYLOAD
                    marker = 1 if offset >= frameSize else 0

                    # seq increases ONCE per packet
                    self.rtpSeqNum = (self.rtpSeqNum + 1) % 65536
                    packet = self.makeRtp(chunk, self.rtpSeqNum, marker)

                    rtpSocket.sendto(packet, (address, port))

                    self.packets_sent += 1
                    self.bytes_sent += len(packet)
                    fragmentsForThisFrame += 1

                self.frames_sent += 1
                self.totalFragments += fragmentsForThisFrame
                if fragmentsForThisFrame > self.maxFragmentsPerFrame:
                    self.maxFragmentsPerFrame = fragmentsForThisFrame

            except Exception as e:
                print(f"[SERVER] Send error: {e}")
                traceback.print_exc()
                break

        print("[SERVER] sendRtp exiting")
        self.printServerStats()

    def makeRtp(self, payload, seqnum, marker=0):
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26  # MJPEG
        ssrc = 123456

        rtpPacket = RtpPacket()
        rtpPacket.encode(
            version, padding, extension, cc,
            seqnum, marker, pt, ssrc,
            payload
        )
        return rtpPacket.getPacket()

    def replyRtsp(self, code, seq):
        if code == self.OK_200:
            reply = f'RTSP/1.0 200 OK\nCSeq: {seq}\nSession: {self.clientInfo["session"]}'
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")

    def printServerStats(self):
        if self.firstSendTime is None:
            return

        duration = max(time() - self.firstSendTime, 1e-6)

        framesSent = self.frames_sent
        packetsSent = self.packets_sent
        bytesSent = self.bytes_sent

        avgPacketSize = (bytesSent / packetsSent) if packetsSent > 0 else 0
        avgBitrateMbps = (bytesSent * 8) / duration / 1_000_000
        avgFragmentsPerFrame = (self.totalFragments / framesSent) if framesSent > 0 else 0
        frameRateSent = framesSent / duration if framesSent > 0 else 0

        print("\n[SERVER STATS]")
        print(f"  Streaming duration  : {duration:.2f} s")
        print(f"  Frames sent         : {framesSent}")
        print(f"  Packets sent        : {packetsSent}")
        print(f"  Bytes sent          : {bytesSent}")
        print(f"  Avg packet size     : {avgPacketSize:.2f} bytes")
        print(f"  Avg bitrate         : {avgBitrateMbps:.2f} Mbps")
        print(f"  Avg fragments/frame : {avgFragmentsPerFrame:.2f}")
        print(f"  Max fragments/frame : {self.maxFragmentsPerFrame}")
        print(f"  Frame rate sent     : {frameRateSent:.2f} fps")
        print(f"  Target FPS          : {TARGET_FPS:.2f} fps\n")