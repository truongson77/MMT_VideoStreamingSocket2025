# <span style="color:#ff5c5c; font-size:32px;">1. Mô tả lỗi</span>
Khi chạy chương trình, server báo lỗi:

**`OSError: [Errno 40] Message too long`**

Lỗi xảy ra khi server gửi một frame có kích thước quá lớn, khiến gói UDP vượt giới hạn cho phép.

**Kết quả:**

- Frame đó không gửi được.  
- Client đứng hình.  
- Sequence number nhảy cóc.  


## <span style="color:#2e7cff; font-size:32px;">2. Nguyên nhân</span>

Code ban đầu dùng quy tắc: **1 frame = 1 RTP packet = 1 UDP packet**  
- Nếu frame nhỏ → gửi được.  
- Nếu frame lớn (ảnh JPEG nhiều KB) → kích thước packet vượt giới hạn → lỗi **“Message too long”**.

# <span style="color:#7ed957; font-size:32px;">3. Cách sửa</span>

Giải pháp: **chia mỗi frame thành nhiều packet nhỏ (fragmentation).**

### **Server**
- Chia frame thành nhiều mảnh (vd: 1400 byte/mảnh).
- Gửi từng packet.
- Gói cuối dùng `marker = 1`.

### **Client**
- Ghép payload của nhiều packet vào buffer.
- Khi gặp `marker = 1` → đã đủ 1 frame → ghi ra file → hiển thị.


# <span style="color:#f8a54f; font-size:32px;">4. Kết quả</span>

- Mỗi packet nhỏ → không vượt giới hạn UDP.  
- Hết lỗi “Message too long”.  
- Video chạy mượt, không đứng hình.  
---


## <span style="color:#c27af0; font-size:28px;">#Marker (0 & 1)</span>

**Marker = 0**

- Packet **chưa phải gói cuối** của frame.  
- Server vẫn còn các phần dữ liệu khác của frame cần gửi tiếp.  
- Client chỉ nối payload vào buffer, **chưa hiển thị** hình.

**Marker = 1**

- Đây là **gói cuối cùng** của frame.  
- Buffer đã chứa **đủ dữ liệu của 1 frame**.  
- Client ghi buffer ra file ảnh và **hiển thị frame**, sau đó reset buffer cho frame tiếp theo.