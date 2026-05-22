# Android / Capacitor 打包预留

`app/` 目录用于后续封装桥智缝卫移动端应用。

建议路线：

```bash
npm create @capacitor/app qzfw-mobile
cd qzfw-mobile
npm install
npx cap add android
```

将云端地址配置为生产服务器，例如：

```text
https://你的域名/mobile
```

当前前端已经提供 `manifest.json` 和 `service-worker.js`，也可以先作为 PWA 使用。
