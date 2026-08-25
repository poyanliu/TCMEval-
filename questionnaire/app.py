import sqlite3
import json
import os
from io import BytesIO
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, send_file
import pandas as pd

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "questionnaire.db")

# All question items extracted from index.html ITEMS
# (element_key, item_id, question_text, is_screening)
ALL_ITEMS = [
    ("wood", "w01", "面色偏青", True), ("wood", "w02", "眼睛细长", False),
    ("wood", "w03", "头部略小", False), ("wood", "w04", "身材偏瘦高", False),
    ("wood", "w05", "肩部较宽", False), ("wood", "w06", "四肢细长", False),
    ("wood", "w07", "手指较细长", False), ("wood", "w08", "手脚灵活", False),
    ("wood", "w09", "性格内向，喜安静", True), ("wood", "w10", "平时容易焦虑", False),
    ("wood", "w11", "容易多愁善感", False), ("wood", "w12", "能适应春夏，不能耐受秋冬", True),
    ("wood", "w13", "对风邪适应和耐受能力差", False), ("wood", "w14", "善于从事脑力劳动", False),
    ("wood", "w15", "好动，但体力稍差", False),
    ("fire", "f01", "面色偏红", True), ("fire", "f02", "脸型偏瘦", False),
    ("fire", "f03", "五官皆小", False), ("fire", "f04", "体型丰满强壮", False),
    ("fire", "f05", "肩背腰腹宽厚", False), ("fire", "f06", "臀部较大", False),
    ("fire", "f07", "大腿肌肉丰满", False), ("fire", "f08", "手足不大，肉厚实", False),
    ("fire", "f09", "手指指根粗、指头尖", False), ("fire", "f10", "是一个有气魄的人", False),
    ("fire", "f11", "平时比较好面子", False), ("fire", "f12", "性格急躁易冲动", True),
    ("fire", "f13", "能适应春夏，不能耐受秋冬", False), ("fire", "f14", "对火邪适应和耐受能力差", True),
    ("fire", "f15", "精气神十足，充满活力", False), ("fire", "f16", "大便常干，小便黄", False),
    ("earth", "e01", "面色偏黄", True), ("earth", "e02", "圆脸型", False),
    ("earth", "e03", "嘴巴大且唇厚", False), ("earth", "e04", "下巴比较宽厚", False),
    ("earth", "e05", "身材魁梧健壮", False), ("earth", "e06", "背部肌肉厚实", False),
    ("earth", "e07", "腹部较大", False), ("earth", "e08", "手足肉较多", False),
    ("earth", "e09", "性格偏稳重敦厚", True), ("earth", "e10", "性情温和", False),
    ("earth", "e11", "忠厚坚定", False), ("earth", "e12", "能适应秋冬，不能耐受春夏", True),
    ("earth", "e13", "对湿邪适应和耐受力差", False), ("earth", "e14", "举足轻而步履稳重", False),
    ("metal", "m01", "面色偏白", True), ("metal", "m02", "脸型偏方", False),
    ("metal", "m03", "眉骨高", False), ("metal", "m04", "眼窝深", False),
    ("metal", "m05", "颧骨高", False), ("metal", "m06", "体型较匀称苗条", False),
    ("metal", "m07", "身材偏瘦（骨架小、关节不突出）", False), ("metal", "m08", "手足偏小", False),
    ("metal", "m09", "手足软，皮肤细腻", False), ("metal", "m10", "精明能干", False),
    ("metal", "m11", "性格刚强坚毅", True), ("metal", "m12", "平时做事果断", False),
    ("metal", "m13", "容易固执己见", False), ("metal", "m14", "能耐受秋冬，不能耐受春夏", True),
    ("metal", "m15", "对于燥邪耐受和适应能力较差", False), ("metal", "m16", "说话声音洪亮", False),
    ("metal", "m17", "行动灵便，动作敏捷", False),
    ("water", "a01", "面色偏黑", True), ("water", "a02", "腮部较宽", False),
    ("water", "a03", "下巴处棱角分明", False), ("water", "a04", "眉毛粗重", False),
    ("water", "a05", "身材偏矮胖", True), ("water", "a06", "毛发浓密、深黑", False),
    ("water", "a07", "手足形肥厚，皮肤光滑有光泽", False), ("water", "a08", "拇指细小", False),
    ("water", "a09", "平时为人较圆滑", False), ("water", "a10", "平时多疑嫉妒", False),
    ("water", "a11", "能耐受秋冬，不能耐受春夏", False), ("water", "a12", "对于寒邪耐受和适应能力较差", True),
    ("water", "a13", "平时比较好动", False),
]

ELEMENT_NAMES = {"wood": "木", "fire": "火", "earth": "土", "metal": "金", "water": "水"}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            submit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            screening_answers TEXT,
            detailed_answers TEXT,
            scores TEXT,
            result_key TEXT,
            result_name TEXT,
            user_agent TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_ip():
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


@app.route("/")
@app.route("/index.html")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.get_json(force=True)
    ip = get_ip()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO submissions
           (ip_address, screening_answers, detailed_answers, scores, result_key, result_name, user_agent)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            ip,
            json.dumps(data.get("screeningAnswers", {}), ensure_ascii=False),
            json.dumps(data.get("detailedAnswers", {}), ensure_ascii=False),
            json.dumps(data.get("scores", {}), ensure_ascii=False),
            data.get("resultKey", ""),
            data.get("resultName", ""),
            request.headers.get("User-Agent", ""),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "ip": ip})


def build_export_df():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT id, ip_address, submit_time, screening_answers, detailed_answers, scores, result_key, result_name FROM submissions ORDER BY id", conn)
    conn.close()

    if df.empty:
        return df

    # Expand each question into its own column
    for elem, qid, qtext, is_screen in ALL_ITEMS:
        col_name = f"[{ELEMENT_NAMES[elem]}] {qtext}"
        def extract(val, qid=qid, row_answers=None):
            pass
        # Build per-question columns
        values = []
        for _, row in df.iterrows():
            screening = json.loads(row["screening_answers"] or "{}")
            detailed = json.loads(row["detailed_answers"] or "{}")
            val = screening.get(qid) or detailed.get(qid) or ""
            values.append(val)
        df[col_name] = values

    # Expand scores into per-element columns
    for key in ["wood", "fire", "earth", "metal", "water"]:
        ename = ELEMENT_NAMES[key]
        for metric in ["total", "avg"]:
            col_name = f"{ename}得分({metric})"
            values = []
            for _, row in df.iterrows():
                scores = json.loads(row["scores"] or "{}")
                values.append(scores.get(key, {}).get(metric, ""))
            df[col_name] = values

    # Rename result column
    df["体质类型"] = df["result_name"]
    df["提交时间"] = df["submit_time"]
    df["IP地址"] = df["ip_address"]

    # Drop raw JSON columns
    df = df.drop(columns=["screening_answers", "detailed_answers", "scores",
                           "result_key", "result_name", "submit_time", "ip_address"])

    return df


@app.route("/api/export")
def export():
    df = build_export_df()
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="问卷提交记录", index=False)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="问卷数据.xlsx",
    )


if __name__ == "__main__":
    init_db()
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6006
    app.run(host="0.0.0.0", port=port)
