# 桥智缝卫

桥智缝卫是一个面向桥梁伸缩缝的智能巡检云端系统，提供小车控制、ESP32-CAM 图像采集、OpenCV ROI 病害识别、复检、报告生成和后台记录查看。

## 项目结构

```text
qiaozhi-fengwei/
├── backend/
│   ├── main.py
│   ├── detector.py
│   ├── database.py
│   ├── report.py
│   ├── requirements.txt
│   └── uploads/.gitkeep
├── frontend/
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   ├── manifest.json
│   └── service-worker.js
├── firmware/
│   └── qzfw_esp32cam.ino
├── app/
│   └── README.md
├── README.md
└── .gitignore
```

## 保留接口

- `GET /health`
- `GET /mobile`
- `POST /api/detect`
- `POST /api/recheck`
- `GET /report/{id}`
- `GET /admin`
- `GET /uploads/{filename}`

## 本地运行

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器访问：

```text
http://127.0.0.1:8000/mobile
```

## 阿里云部署

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nginx
cd /opt/qiaozhi-fengwei/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

生产环境建议使用 systemd 托管：

```bash
sudo tee /etc/systemd/system/qzfw.service >/dev/null <<'EOF'
[Unit]
Description=Qiaozhi Fengwei FastAPI
After=network.target

[Service]
WorkingDirectory=/opt/qiaozhi-fengwei/backend
Environment=PATH=/opt/qiaozhi-fengwei/backend/venv/bin
ExecStart=/opt/qiaozhi-fengwei/backend/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now qzfw
```

Nginx 反向代理示例：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## ESP32-CAM 接口约定

固件需提供：

```text
/forward
/backward
/left
/right
/stop
/capture
/capture_hq
/led/on
/led/off
/status
/reset
```

前端“拍照采集”按钮调用 `captureFromCar()`，从 ESP32-CAM 的 `/capture_hq` 获取图片，再上传到云端 `/api/detect`。
