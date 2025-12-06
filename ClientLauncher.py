import sys
from tkinter import Tk
from Client import Client

if __name__ == "__main__":
    try:
        serverAddr = sys.argv[1]
        serverPort = sys.argv[2]
        rtpPort    = sys.argv[3]
        fileName   = sys.argv[4]
    except:
        print("[Usage: ClientLauncher.py Server_name Server_port RTP_port Video_file]\n")
        sys.exit(0)

    root = Tk()
    root.title("RTPClient")

    # Truyền root trực tiếp vào Client
    app = Client(root, serverAddr, serverPort, rtpPort, fileName)

    root.mainloop()