# Project Socket Programming Video Streaming with RTSP and RTP

## Overview
In this lab you will implement a streaming video server and client that communicate using the
Real-Time Streaming Protocol (RTSP) and send data using the Real-time Transfer Protocol
(RTP).
Your task is to implement the RTSP protocol in the client and implement the RTP packetization
in the server. We will provide you code that implements the RTSP protocol in the server, the
RTP de-packetization in the client, and takes care of displaying the transmitted video.

---

## Requirements
- Python 3.10+ (adjust to your project’s version)
- `pip` package manager

---

## Setup

1. **Create a virtual environment** (recommended to isolate dependencies):

```bash
python -m venv venv

## Run Project
- Server.py
```bash
python3 Server.py 8554  

- ClientLauncher.py
```bash
python3 ClientLauncher.py localhost 8554 25000 movie.Mjpeg