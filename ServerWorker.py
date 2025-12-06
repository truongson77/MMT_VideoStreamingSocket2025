from random import randint
import sys, traceback, threading, socket
from time import time  # dùng hàm time()

from VideoStream import VideoStream
from RtpPacket import RtpPacket

MAX_PAYLOAD = 1400  # bytes


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

        # Thống kê gửi
        self.bytes_sent = 0
        self.packets_sent = 0
        self.packetSeq = 0
        self.firstSendTime = None

        # Thống kê fragmentation
        self.totalFragments = 0      # tổng số mảnh (RTP packets)
        self.maxFragmentsPerFrame = 0

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

        # media file
        filename = line1[1]

        # RTSP sequence number
        seq = request[1].split(' ')

        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("processing SETUP\n")
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
                    return

                # random RTSP session ID
                self.clientInfo['session'] = randint(100000, 999999)

                # Send RTSP reply
                self.replyRtsp(self.OK_200, seq[1])

                # RTP/UDP port (last line)
                self.clientInfo['rtpPort'] = request[2].split(' ')[3]

        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY\n")
                self.state = self.PLAYING

                # Create RTP/UDP socket
                self.clientInfo["rtpSocket"] = socket.socket(
                    socket.AF_INET, socket.SOCK_DGRAM
                )

                self.replyRtsp(self.OK_200, seq[1])

                # Start sending RTP
                self.clientInfo['event'] = threading.Event()
                self.clientInfo['worker'] = threading.Thread(target=self.sendRtp)
                self.clientInfo['worker'].start()

        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE\n")
                self.state = self.READY
                self.clientInfo['event'].set()
                self.printServerStats()
                self.replyRtsp(self.OK_200, seq[1])

        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN\n")
            self.clientInfo['event'].set()
            self.replyRtsp(self.OK_200, seq[1])

            if "rtpSocket" in self.clientInfo:
                self.clientInfo['rtpSocket'].close()

    def sendRtp(self):
        """Send RTP packets over UDP (fragment per packet, seq theo packet)."""

        # Reset thống kê mỗi lần PLAY
        self.packetSeq = 0
        self.packets_sent = 0
        self.bytes_sent = 0
        self.totalFragments = 0
        self.maxFragmentsPerFrame = 0
        self.firstSendTime = time()

        while True:
            # 0.05s ~ 20 fps (tuỳ video)
            self.clientInfo['event'].wait(0.05)

            # Nếu PAUSE / TEARDOWN thì dừng
            if self.clientInfo['event'].isSet():
                break

            # Lấy frame tiếp theo
            data = self.clientInfo['videoStream'].nextFrame()
            if not data:
                # Hết video
                break

            frameNumber = self.clientInfo['videoStream'].frameNbr()
            frameSize = len(data)
            print(f"Frame {frameNumber} - size: {frameSize} bytes")

            try:
                address = self.clientInfo['rtspSocket'][1][0]
                port = int(self.clientInfo['rtpPort'])

                # Đếm số mảnh cho frame này
                fragments_for_this_frame = 0

                # Chia frame thành nhiều chunk <= MAX_PAYLOAD
                offset = 0
                while offset < frameSize:
                    chunk = data[offset: offset + MAX_PAYLOAD]
                    offset += MAX_PAYLOAD

                    # Gói cuối cùng của frame → marker = 1
                    marker = 1 if offset >= frameSize else 0

                    # Sequence number tăng theo từng packet
                    self.packetSeq += 1
                    packet = self.makeRtp(chunk, self.packetSeq, marker)

                    # Gửi gói
                    self.clientInfo['rtpSocket'].sendto(packet, (address, port))

                    # Cập nhật thống kê packet
                    self.packets_sent += 1
                    self.bytes_sent += len(packet)
                    fragments_for_this_frame += 1

                # Cập nhật thống kê fragmentation
                self.totalFragments += fragments_for_this_frame
                if fragments_for_this_frame > self.maxFragmentsPerFrame:
                    self.maxFragmentsPerFrame = fragments_for_this_frame

            except Exception as e:
                print(f"Connection Error at frame {frameNumber}: {e}")
                traceback.print_exc()
                self.clientInfo['event'].set()
                break

        # Khi thoát vòng while (PAUSE / TEARDOWN / hết video) → in thống kê
        self.printServerStats()
        
    def makeRtp(self, payload, seqnum, marker):
        """RTP-packetize the video data."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26  # MJPEG
        ssrc = 0

        rtpPacket = RtpPacket()
        rtpPacket.encode(
            version, padding, extension, cc,
            seqnum, marker, pt, ssrc,
            payload
        )
        return rtpPacket.getPacket()

    def replyRtsp(self, code, seq):
        """Send RTSP reply to the client."""
        if code == self.OK_200:
            reply = (
                "RTSP/1.0 200 OK\n"
                f"CSeq: {seq}\n"
                f"Session: {self.clientInfo['session']}"
            )
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())

        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")
            
    def printServerStats(self):
        """In thống kê khi kết thúc streaming."""
        if self.firstSendTime is None:
            return

        duration = max(time() - self.firstSendTime, 1e-6)
        frames_sent = self.clientInfo['videoStream'].frameNbr()

        # Tránh chia 0
        avg_packet_size = (self.bytes_sent / self.packets_sent) if self.packets_sent > 0 else 0
        avg_bitrate_mbps = (self.bytes_sent * 8) / duration / 1_000_000
        avg_fragments_per_frame = (self.totalFragments / frames_sent) if frames_sent > 0 else 0
        frame_rate_sent = frames_sent / duration

        print("\n[SERVER STATS]")
        print(f"  Streaming duration  : {duration:.2f} s")
        print(f"  Frames sent         : {frames_sent}")
        print(f"  Packets sent        : {self.packets_sent}")
        print(f"  Bytes sent          : {self.bytes_sent}")
        print(f"  Avg packet size     : {avg_packet_size:.2f} bytes")
        print(f"  Avg bitrate         : {avg_bitrate_mbps:.2f} Mbps")
        print(f"  Avg fragments/frame : {avg_fragments_per_frame:.2f}")
        print(f"  Max fragments/frame : {self.maxFragmentsPerFrame}")
        print(f"  Frame rate sent     : {frame_rate_sent:.2f} fps\n")