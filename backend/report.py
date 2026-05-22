import html
import sqlite3


def file_url(filename: str) -> str:
    safe = filename.replace("/", "_").replace("\\", "_").replace("..", "_")
    return f"/uploads/{safe}"


def risk_class(risk_level: str) -> str:
    if risk_level == "中风险":
        return "mid"
    if risk_level == "高风险":
        return "high"
    return "low"


def report_page(row: sqlite3.Row, rechecks: list[sqlite3.Row]) -> str:
    recheck_html = ""
    if rechecks:
        for item in rechecks:
            recheck_html += f"""
            <div class="recheck">
                <h3>复检记录：{html.escape(item["id"])}</h3>
                <p>复检时间：{html.escape(item["created_at"])}</p>
                <p>修复前面积占比：{item["before_area_ratio"]}%</p>
                <p>修复后面积占比：{item["after_area_ratio"]}%</p>
                <p>改善率：{item["improvement_rate"]}%</p>
                <p>验收结果：{html.escape(item["acceptance_result"])}</p>
                <p>建议：{html.escape(item["suggestion"])}</p>
                <img src="{file_url(item["after_analysis_filename"])}" alt="复检分析图">
            </div>
            """
    else:
        recheck_html = "<p>暂无复检记录。后续可通过 /api/recheck 上传修复后图片。</p>"

    yes_no = "是" if row["repairable"] else "否"
    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>桥智缝卫检测报告 - {html.escape(row["id"])}</title>
    <link rel="stylesheet" href="/static/style.css">
    </head>
    <body class="page-report">
    <main class="report-wrap">
        <h1>桥智缝卫伸缩缝智能巡检报告</h1>
        <section class="report-panel">
            <p>巡检编号：{html.escape(row["id"])}</p>
            <p>巡检时间：{html.escape(row["created_at"])}</p>
            <p>病害类型：{html.escape(row["disease_type"])}</p>
            <p>病害等级：{html.escape(row["disease_level"])}</p>
            <p>风险等级：<span class="risk {risk_class(row["risk_level"])}">{html.escape(row["risk_level"])}</span></p>
            <p>疑似区域数量：{row["suspect_count"]}</p>
            <p>面积占比：{row["area_ratio"]}%</p>
            <p>识别置信度：{row["confidence"]}</p>
            <p>修复决策：{html.escape(row["decision"])}</p>
            <p>是否建议修复：{yes_no}</p>
            <p>处理建议：{html.escape(row["suggestion"])}</p>
        </section>
        <section class="report-grid">
            <div><h2>原始巡检图</h2><img src="{file_url(row["raw_filename"])}" alt="原始巡检图"></div>
            <div><h2>云端分析图</h2><img src="{file_url(row["analysis_filename"])}" alt="云端分析图"></div>
        </section>
        <h2>复检记录</h2>
        {recheck_html}
        <p class="report-links"><a href="/mobile">返回手机端页面</a><a href="/admin">进入后台记录</a></p>
    </main>
    </body>
    </html>
    """


def admin_page(rows: list[sqlite3.Row]) -> str:
    cards = ""
    for row in rows:
        color = "#42ff9c"
        if row["risk_level"] == "中风险":
            color = "#ffcd48"
        elif row["risk_level"] == "高风险":
            color = "#ff6b6b"
        cards += f"""
        <tr>
            <td>{html.escape(row["created_at"])}</td>
            <td>{html.escape(row["id"])}</td>
            <td>{html.escape(row["disease_type"])}</td>
            <td style="color:{color};font-weight:900">{html.escape(row["risk_level"])}</td>
            <td>{row["suspect_count"]}</td>
            <td>{row["area_ratio"]}%</td>
            <td><a href="/report/{html.escape(row["id"])}" target="_blank">查看报告</a></td>
        </tr>
        """
    if not cards:
        cards = '<tr><td colspan="7" class="empty">暂无检测记录，请先到 /mobile 上传图片。</td></tr>'

    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>桥智缝卫后台</title>
    <link rel="stylesheet" href="/static/style.css">
    </head>
    <body class="page-admin">
        <main class="admin-wrap">
            <div class="admin-top">
                <h1>桥智缝卫云端后台记录</h1>
                <a class="admin-btn" href="/mobile">返回手机端</a>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>时间</th>
                        <th>巡检编号</th>
                        <th>病害类型</th>
                        <th>风险等级</th>
                        <th>疑似区域</th>
                        <th>面积占比</th>
                        <th>报告</th>
                    </tr>
                </thead>
                <tbody>{cards}</tbody>
            </table>
        </main>
    </body>
    </html>
    """
