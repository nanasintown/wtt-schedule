# Ứng dụng lịch thi đấu WTT

Ứng dụng Streamlit tải lịch thi đấu từ World Table Tennis và hiển thị các trận có liên quan đến Wang Chuqin hoặc Sun Yingsha.

Với sự kiện `3242`, múi giờ địa điểm mặc định là `America/Los_Angeles`. Thời gian được chuyển sang giờ Helsinki, Việt Nam và Hàn Quốc.

## Chạy ứng dụng

```bash
cd /Users/nhutmyc/wtt-schedule-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

Sau đó mở `http://localhost:8501` trên trình duyệt.

Ứng dụng tự cập nhật lịch thi đấu mỗi 12 giờ. WTT có thể thay đổi lịch, vì vậy nên kiểm tra lại trước khi xem trận.

## Deploy để chia sẻ với bạn bè

Ứng dụng sử dụng Playwright và Chromium, vì vậy nên deploy bằng Docker trên Render.

### 1. Đưa code lên GitHub

Tạo một repository GitHub mới và upload các file sau:

```text
app.py
requirements.txt
Dockerfile
render.yaml
.dockerignore
```

### 2. Tạo Web Service trên Render

1. Mở [Render Dashboard](https://dashboard.render.com/) và đăng nhập bằng GitHub.
2. Chọn **New → Web Service**.
3. Chọn repository GitHub của ứng dụng.
4. Chọn runtime **Docker**.
5. Chọn gói **Free** nếu chỉ cần chia sẻ cá nhân.
6. Nhấn **Create Web Service**.

Render sẽ đọc `Dockerfile`, cài Chromium và chạy ứng dụng. Sau khi deploy thành công, Render sẽ cấp một URL dạng:

```text
https://wtt-us-smash-schedule.onrender.com
```

Bạn có thể gửi URL này cho bạn bè.

Gói miễn phí của Render có thể tạm dừng service khi không có người truy cập, nên lần mở đầu tiên đôi khi sẽ mất vài giây. Khi cập nhật code trên GitHub, Render có thể tự deploy lại theo branch đã kết nối.
