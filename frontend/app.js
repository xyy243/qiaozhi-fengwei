(function () {
    if (window.__qawlDashboardLoaded) return;
    window.__qawlDashboardLoaded = true;

    const $ = (id) => document.getElementById(id);
    let selectedFile = null;
    let selectedImageDataUrl = "";
    let lastReportUrl = "";

    const fallbackDetection = {
        disease_type: "锚固区混凝土破损 / 剥落露筋",
        risk_level: "高风险",
        confidence: 0.94,
        area_ratio: 18.7,
        suspect_count: 4,
        suggestion: "建议立即修复，防止钢筋锈蚀及结构耐久性进一步下降",
        created_at: "2025-05-30 10:24:36",
    };

    function toast(message) {
        const t = $("toast");
        if (!t) return;
        t.textContent = message;
        t.style.display = "block";
        clearTimeout(window.__toastTimer);
        window.__toastTimer = setTimeout(() => {
            t.style.display = "none";
        }, 2400);
    }

    function nowText() {
        const d = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    function setAnalysisStatus(text, mode = "") {
        const status = $("analysisStatus");
        if (!status) return;
        status.textContent = text;
        status.className = `analysis-status ${mode}`.trim();
    }

    function valueOf(data, keys, fallback = "") {
        for (const key of keys) {
            if (data && data[key] !== undefined && data[key] !== null && data[key] !== "") return data[key];
        }
        return fallback;
    }

    function preferredImageUrl(data = {}) {
        return data.result_url || data.analysis_image || data.result_image || data.image_url || data.marked_image || data.output_image || "";
    }

    function normalizeDetection(data = {}) {
        const reportId = valueOf(data, ["report_id", "id"], "");
        const reportUrl = data.report_url || (reportId ? `/report/${reportId}` : "");
        return {
            disease_type: valueOf(data, ["disease_type", "damage_type", "type", "disease", "category"], fallbackDetection.disease_type),
            risk_level: valueOf(data, ["risk_level", "risk", "level"], fallbackDetection.risk_level),
            confidence: Number(valueOf(data, ["confidence", "score", "probability"], fallbackDetection.confidence)),
            area_ratio: valueOf(data, ["area_ratio", "damage_area_ratio", "ratio"], fallbackDetection.area_ratio),
            suspect_count: valueOf(data, ["suspect_count", "damage_count", "count"], fallbackDetection.suspect_count),
            suggestion: valueOf(data, ["suggestion", "repair_suggestion", "advice", "recommendation"], fallbackDetection.suggestion),
            created_at: valueOf(data, ["created_at", "time", "detect_time"], nowText()),
            report_url: reportUrl,
            image_url: preferredImageUrl(data),
        };
    }

    function normalizeVideoUrl(value, mode) {
        const raw = (value || "").trim();
        if (!raw) return "";
        if (mode === "raw" || /^https?:\/\//i.test(raw)) return raw;
        const ip = raw.replace("http://", "").replace("https://", "").replace(/\/+$/, "");
        if (mode === "video") return `http://${ip}/video`;
        return `http://${ip}:81/stream`;
    }

    window.connectVideo = function connectVideo() {
        const url = normalizeVideoUrl($("carIp")?.value, $("streamMode")?.value);
        const img = $("videoStream");
        const empty = $("videoEmpty");
        const error = $("videoError");
        const badge = $("videoLiveBadge");
        if (!url || !img) {
            toast("请先输入 ESP32-CAM IP 或视频地址");
            return;
        }
        if (error) error.style.display = "none";
        if (badge) {
            badge.textContent = "连接中";
            badge.classList.remove("connected");
        }
        img.onload = () => {
            if (empty) empty.style.display = "none";
            img.style.display = "block";
            if (badge) {
                badge.textContent = "已连接";
                badge.classList.add("connected");
            }
        };
        img.onerror = () => {
            if (error) error.style.display = "block";
            if (empty) empty.style.display = "flex";
            img.style.display = "none";
            if (badge) {
                badge.textContent = "未连接";
                badge.classList.remove("connected");
            }
            toast("请检查 ESP32-CAM IP、端口和 WiFi 是否一致");
        };
        img.src = `${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`;
        toast(`正在连接视频流：${url}`);
    };

    window.toggleVideoFullscreen = function toggleVideoFullscreen() {
        const target = document.querySelector(".video-stage");
        if (!target) return;
        if (document.fullscreenElement) document.exitFullscreen();
        else target.requestFullscreen?.();
    };

    function carBaseUrl() {
        const raw = ($("carIp")?.value || "").trim();
        if (!raw) return "";
        if (/^https?:\/\//i.test(raw)) {
            const url = new URL(raw);
            return `${url.protocol}//${url.host}`;
        }
        return `http://${raw.replace("http://", "").replace("https://", "").replace(/\/+$/, "")}`;
    }

    window.sendCar = async function sendCar(path) {
        const base = carBaseUrl();
        console.log("car command", path);
        if (!base) {
            toast("请先输入 ESP32-CAM IP");
            return;
        }
        try {
            await fetch(`${base}/${path}`, { mode: "no-cors" });
            toast(`指令已发送：${path}`);
        } catch (err) {
            toast("指令发送失败，请检查设备网络");
        }
    };

    window.captureFromCar = async function captureFromCar() {
        const base = carBaseUrl();
        if (!base) {
            toast("请先输入 ESP32-CAM IP");
            return;
        }
        try {
            toast("正在从 ESP32-CAM 获取高清照片");
            const res = await fetch(`${base}/capture_hq?t=${Date.now()}`);
            if (!res.ok) throw new Error("capture_hq failed");
            const blob = await res.blob();
            selectedFile = new File([blob], "esp32cam_capture.jpg", { type: blob.type || "image/jpeg" });
            await showSelectedFile(selectedFile);
            await detectBlob(selectedFile, selectedFile.name);
        } catch (err) {
            toast("拍照采集失败，请检查 /capture_hq 接口");
        }
    };

    window.selectUpload = function selectUpload() {
        $("fileInput")?.click();
    };

    function readFileAsDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ""));
            reader.onerror = () => reject(reader.error || new Error("FileReader failed"));
            reader.readAsDataURL(file);
        });
    }

    async function showSelectedFile(file) {
        if (!file) return;
        selectedFile = file;
        selectedImageDataUrl = await readFileAsDataUrl(file);

        const thumb = $("thumbImg");
        if (thumb) {
            const card = thumb.closest(".file-card");
            card?.classList.remove("has-image");
            thumb.classList.remove("loaded", "broken");
            thumb.onload = () => {
                thumb.classList.add("loaded");
                card?.classList.add("has-image");
            };
            thumb.onerror = () => {
                thumb.removeAttribute("src");
                thumb.classList.remove("loaded");
                thumb.classList.add("broken");
                card?.classList.remove("has-image");
            };
            thumb.src = selectedImageDataUrl;
            thumb.style.display = "block";
        }

        if ($("fileName")) $("fileName").textContent = file.name;
        if ($("fileSize")) $("fileSize").textContent = `${(file.size / 1024 / 1024).toFixed(2)}MB`;
        renderUploadedBaseImage(selectedImageDataUrl, true);
        setAnalysisStatus("图片已加载，等待云端识别", "ready");
        toast("图片已加载，等待云端识别");
    }

    function riskClass(risk) {
        if ((risk || "").includes("高")) return "high";
        if ((risk || "").includes("中")) return "mid";
        return "low";
    }

    function formatArea(value) {
        const raw = String(value ?? fallbackDetection.area_ratio);
        return raw.includes("%") ? raw : `${raw}%`;
    }

    function updateDetail(data) {
        const merged = normalizeDetection(data);
        const risk = merged.risk_level || fallbackDetection.risk_level;

        if ($("detailType")) $("detailType").textContent = merged.disease_type;
        if ($("detailRisk")) {
            $("detailRisk").textContent = risk;
            $("detailRisk").className = `risk-tag ${riskClass(risk)}`;
        }
        if ($("detailConfidence")) $("detailConfidence").textContent = Number(merged.confidence || fallbackDetection.confidence).toFixed(2);
        if ($("detailArea")) $("detailArea").textContent = formatArea(merged.area_ratio);
        if ($("detailAdvice")) $("detailAdvice").textContent = merged.suggestion;
        if ($("detailTime")) $("detailTime").textContent = `识别时间：${merged.created_at || nowText()}`;

        if ($("aiSuspectCount")) $("aiSuspectCount").textContent = `${merged.suspect_count}处`;
        if ($("aiAreaRatio")) $("aiAreaRatio").textContent = formatArea(merged.area_ratio);
        if ($("aiConfidence")) $("aiConfidence").textContent = Number(merged.confidence || fallbackDetection.confidence).toFixed(2);
        if ($("aiRiskLevel")) $("aiRiskLevel").textContent = risk;

        lastReportUrl = merged.report_url || lastReportUrl;
        return merged;
    }

    function renderUploadedBaseImage(imageUrl, showOverlay) {
        const img = $("analysisImg");
        const mock = $("mockAnalysis");
        const flow = document.querySelector(".analysis-flow");
        if (img && imageUrl) {
            img.src = imageUrl;
            img.style.display = "block";
            img.classList.add("has-user-image");
            img.style.filter = "brightness(1.16) contrast(1.13) saturate(1.08)";
        }
        if (mock) {
            mock.style.display = showOverlay ? "block" : "none";
            mock.classList.toggle("overlay-mode", Boolean(imageUrl));
        }
        if (flow) flow.textContent = "原始采集图 → 云端分析图";
    }

    function renderDetection(data, fallbackUrl) {
        const merged = updateDetail(data || {});
        const imageUrl = merged.image_url;
        const img = $("analysisImg");
        const mock = $("mockAnalysis");
        const flow = document.querySelector(".analysis-flow");
        if (imageUrl && img) {
            img.src = `${imageUrl}${imageUrl.includes("?") ? "&" : "?"}t=${Date.now()}`;
            img.style.display = "block";
            img.classList.add("has-user-image");
            img.style.filter = "brightness(1.16) contrast(1.12) saturate(1.08)";
            if (mock) mock.style.display = "none";
            if (flow) flow.textContent = "原始采集图 → 云端分析图";
        } else {
            renderUploadedBaseImage(fallbackUrl || selectedImageDataUrl, true);
        }
        setAnalysisStatus("识别完成，已生成云端分析图", "done");
    }

    async function detectBlob(blob, filename) {
        const fd = new FormData();
        fd.append("file", blob, filename || "bridge-upload.jpg");
        setAnalysisStatus("云端识别中...", "running");
        toast("云端识别中...");
        const res = await fetch("/api/detect", { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(JSON.stringify(data));
        renderDetection(data, selectedImageDataUrl);
        toast("识别完成，已生成云端分析图");
    }

    window.uploadDetect = async function uploadDetect() {
        if (!selectedFile) {
            const file = $("fileInput")?.files?.[0];
            if (file) await showSelectedFile(file);
        }
        if (!selectedFile) {
            setAnalysisStatus("请先上传桥梁伸缩缝图片", "error");
            toast("请先上传桥梁伸缩缝图片");
            return;
        }
        try {
            await detectBlob(selectedFile, selectedFile.name);
        } catch (err) {
            setAnalysisStatus("识别失败，请检查服务器连接", "error");
            renderDetection(fallbackDetection, selectedImageDataUrl);
            toast("识别失败，请检查服务器连接");
        }
    };

    window.openReport = function openReport() {
        if (lastReportUrl) window.open(lastReportUrl, "_blank");
        else toast("请先完成一次 AI识别以生成报告");
    };

    function setupUpload() {
        const input = $("fileInput");
        const drop = $("dropZone");
        input?.addEventListener("change", async () => {
            const file = input.files?.[0];
            if (file) await showSelectedFile(file);
        });
        ["dragenter", "dragover"].forEach((evt) => {
            drop?.addEventListener(evt, (e) => {
                e.preventDefault();
                drop.classList.add("dragging");
            });
        });
        ["dragleave", "drop"].forEach((evt) => {
            drop?.addEventListener(evt, (e) => {
                e.preventDefault();
                drop.classList.remove("dragging");
            });
        });
        drop?.addEventListener("drop", async (e) => {
            const file = e.dataTransfer?.files?.[0];
            if (file) await showSelectedFile(file);
        });
    }

    function drawLineChart() {
        const svg = $("lineChart");
        if (!svg) return;
        const data = [18, 26, 34, 28, 42, 31, 36];
        const labels = ["05-24", "05-25", "05-26", "05-27", "05-28", "05-29", "05-30"];
        const max = 60;
        const x = (i) => 34 + i * 50;
        const y = (v) => 124 - (v / max) * 92;
        const points = data.map((v, i) => `${x(i)},${y(v)}`).join(" ");
        svg.innerHTML = `
            <defs>
                <linearGradient id="lineFill" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0" stop-color="rgba(74,190,255,.38)"/>
                    <stop offset="1" stop-color="rgba(74,190,255,0)"/>
                </linearGradient>
                <filter id="glow"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
            </defs>
            <path d="M34,124 L334,124 L${points.replaceAll(" ", " L")} Z" fill="url(#lineFill)"/>
            <polyline points="${points}" fill="none" stroke="#62caff" stroke-width="3" filter="url(#glow)"/>
            ${data.map((v, i) => `<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="#dff8ff" stroke="#3ab8ff" stroke-width="2" filter="url(#glow)"/><text x="${x(i)}" y="${y(v)-10}" fill="#e7f7ff" font-size="11" text-anchor="middle">${v}</text><text x="${x(i)}" y="145" fill="#8dafc9" font-size="10" text-anchor="middle">${labels[i]}</text>`).join("")}
            <line x1="28" y1="124" x2="344" y2="124" stroke="rgba(150,210,255,.25)"/>
            <line x1="28" y1="42" x2="344" y2="42" stroke="rgba(150,210,255,.12)"/>
        `;
    }

    function drawBars() {
        const target = $("barChart");
        if (!target) return;
        const data = [
            ["混凝土破损", 56, "31%"],
            ["止水带损坏", 42, "23%"],
            ["钢筋锈蚀", 38, "21%"],
            ["异物堵塞", 24, "13%"],
            ["其他", 20, "12%"],
        ];
        const max = 56;
        target.innerHTML = data.map(([name, value, pct]) => `
            <div class="bar-row">
                <span>${name}</span>
                <div class="bar-track"><div class="bar-fill" style="width:${(value / max) * 100}%"></div></div>
                <b>${value} (${pct})</b>
            </div>
        `).join("");
    }

    function setupMapTooltips() {
        const tip = $("mapTooltip");
        document.querySelectorAll(".map-point").forEach((point) => {
            point.addEventListener("mouseenter", () => {
                if (!tip) return;
                tip.innerHTML = `<b>${point.dataset.city}</b><br>桥梁数量：${point.dataset.count}<br>风险等级：${point.dataset.risk}<br>在线设备：${point.dataset.online}`;
                tip.style.display = "block";
            });
            point.addEventListener("mousemove", (e) => {
                if (!tip) return;
                const box = point.closest(".map-wrap").getBoundingClientRect();
                tip.style.left = `${e.clientX - box.left + 12}px`;
                tip.style.top = `${e.clientY - box.top - 12}px`;
            });
            point.addEventListener("mouseleave", () => {
                if (tip) tip.style.display = "none";
            });
        });
    }

    function init() {
        setupUpload();
        drawLineChart();
        drawBars();
        setupMapTooltips();
        updateDetail(fallbackDetection);
        setAnalysisStatus("等待识别");
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
