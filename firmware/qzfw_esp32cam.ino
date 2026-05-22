/*
  桥智缝卫 ESP32-CAM 接口约定

  云端前端会调用以下 HTTP 接口：
  /forward、/backward、/left、/right、/stop
  /capture、/capture_hq
  /led/on、/led/off
  /status、/reset

  本文件保留接口契约和固件扩展位置。请根据实际电机驱动、
  摄像头型号、WiFi 配置和引脚定义补充具体实现。
*/

#include <Arduino.h>
#include <WebServer.h>

WebServer server(80);

void sendOk(const String &action) {
  server.send(200, "application/json", "{\"ok\":true,\"action\":\"" + action + "\"}");
}

void setup() {
  Serial.begin(115200);

  server.on("/forward", []() { sendOk("forward"); });
  server.on("/backward", []() { sendOk("backward"); });
  server.on("/left", []() { sendOk("left"); });
  server.on("/right", []() { sendOk("right"); });
  server.on("/stop", []() { sendOk("stop"); });
  server.on("/led/on", []() { sendOk("led/on"); });
  server.on("/led/off", []() { sendOk("led/off"); });
  server.on("/status", []() { sendOk("status"); });
  server.on("/reset", []() {
    sendOk("reset");
    delay(200);
    ESP.restart();
  });

  server.on("/capture", []() {
    server.send(501, "application/json", "{\"ok\":false,\"message\":\"capture not implemented\"}");
  });

  server.on("/capture_hq", []() {
    server.send(501, "application/json", "{\"ok\":false,\"message\":\"capture_hq not implemented\"}");
  });

  server.begin();
}

void loop() {
  server.handleClient();
}
