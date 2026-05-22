# 桥智缝卫

桥智缝卫是一个面向桥梁伸缩缝的智能巡检云端系统。

当前功能：
- FastAPI 云端后台
- 手机端 /mobile 页面
- ESP32-CAM 视频与小车控制
- 图片上传识别
- OpenCV ROI 病害识别
- 原图与分析图显示
- 检测报告生成
- 后台记录查看

当前运行方式：

```bash
cd backend
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000

