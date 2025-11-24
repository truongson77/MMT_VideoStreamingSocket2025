from random import randint
import sys, traceback, threading, socket
import math

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'
    
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2
    
    clientInfo = {}
    
    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        
    def run(self):
        threading.Thread(target=self.recvRtspRequest).start()
    
    def recvRtspRequest(self):
        """Receive RTSP request from the client."""
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:            
            data = connSocket.recv(256)
            if data:
                print("Data received:\n" + data.decode("utf-8"))
                self.processRtspRequest(data.decode("utf-8"))
    
    def processRtspRequest(self, data):
        """Process RTSP request sent from the client."""
        request = data.split('\n')
        line1 = request[0].split(' ')
        requestType = line1[0]
        
        filename = line1[1]
        
        seq = request[1].split(' ')
        
        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("processing SETUP\n")
                
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
                
                self.clientInfo['session'] = randint(100000, 999999)
                
                self.replyRtsp(self.OK_200, seq[1])
                
                self.clientInfo['rtpPort'] = request[2].split(' ')[3]
        
        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY\n")
                self.state = self.PLAYING
                
                self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                
                self.replyRtsp(self.OK_200, seq[1])
                
                self.clientInfo['event'] = threading.Event()
                self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
                self.clientInfo['worker'].start()
        
        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE\n")
                self.state = self.READY
                
                self.clientInfo['event'].set()
            
                self.replyRtsp(self.OK_200, seq[1])
        
        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN\n")

            self.clientInfo['event'].set()
            
            self.replyRtsp(self.OK_200, seq[1])
            
            self.clientInfo['rtpSocket'].close()
            
    # -------------------------------------------------------------------
    #  SEND RTP — VERSION WITH FRAGMENTATION SUPPORT
    # -------------------------------------------------------------------
    def sendRtp(self):
        """Send RTP packets over UDP (supports fragmentation)."""

        # sequence number cho từng packet, không reset theo frame
        if not hasattr(self, "seqNum"):
            self.seqNum = 0

        MAX_PAYLOAD = 1400   # giới hạn payload trong mỗi gói RTP

        while True:
            self.clientInfo['event'].wait(0.05) 

            if self.clientInfo['event'].isSet(): 
                break 
                
            data = self.clientInfo['videoStream'].nextFrame()
            if not data:
                continue

            frameNumber = self.clientInfo['videoStream'].frameNbr()

            # số mảnh cần gửi
            total_frag = math.ceil(len(data) / MAX_PAYLOAD)

            for frag_idx in range(total_frag):
                start = frag_idx * MAX_PAYLOAD
                end = min((frag_idx + 1) * MAX_PAYLOAD, len(data))
                payload = data[start:end]

                # marker = 1 nếu là mảnh cuối
                marker = 1 if frag_idx == total_frag - 1 else 0

                try:
                    address = self.clientInfo['rtspSocket'][1][0]
                    port = int(self.clientInfo['rtpPort'])

                    packet = self.makeRtp(payload, self.seqNum, marker)
                    self.clientInfo['rtpSocket'].sendto(packet, (address, port))

                    self.seqNum += 1

                except Exception as e:
                    print(f"Connection Error at frame {frameNumber}, seq {self.seqNum}: {e}")
                    traceback.print_exc()
                    self.clientInfo['event'].set()
                    break
            
            if self.clientInfo['event'].isSet():
                break

    # sửa makeRtp để nhận seqnum + marker
    def makeRtp(self, payload, seqnum, marker):
        """RTP-packetize the video data."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26 # MJPEG type
        ssrc = 0 
        
        rtpPacket = RtpPacket()
        
        rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
        
        return rtpPacket.getPacket()
        
    def replyRtsp(self, code, seq):
        """Send RTSP reply to the client."""
        if code == self.OK_200:
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())
        
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")