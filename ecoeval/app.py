import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import docx
import fitz  # PyMuPDF
from flask import Flask, request, jsonify, send_from_directory, g, session
from openai import OpenAI
from werkzeug.utils import secure_filename

# Load .env file (if present) — keeps secrets out of source control
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _k, _v = _k.strip(), _v.strip().strip("\"'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB
app.config["UPLOAD_FOLDER"] = "/root/EcoEval/uploads"
app.config["SECRET_KEY"] = os.environ.get("ECOEVAL_SECRET_KEY", "ecoeval-secret-key-2026")

ALLOWED_EXTENSIONS = {"docx", "pdf", "doc"}

# DeepSeek API configuration
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

DB_PATH = os.environ.get("ECOEVAL_DB_PATH", "/root/EcoEval/evaluations.db")

# 主系统用户数据库（共享账号体系），可通过环境变量覆盖路径
MAIN_DB_PATH = os.environ.get("TCM_MAIN_DB_PATH", "/root/web/data/evaluations.db")


def verify_user(username: str, password: str) -> bool:
    """验证用户（复用主系统 users 表，PBKDF2-HMAC-SHA256）"""
    import hashlib
    try:
        conn = sqlite3.connect(MAIN_DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT password_hash, salt FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()
        if row is None:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), row["salt"].encode("utf-8"), 100000,
        )
        return digest.hex() == row["password_hash"]
    except Exception:
        return False


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_content TEXT NOT NULL,
                file_data BLOB,
                score_json TEXT NOT NULL,
                total_score REAL NOT NULL,
                dimension_scores TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text(filepath, ext):
    if ext == "docx":
        doc = docx.Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        tables_text = []
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells)
                tables_text.append(row_text)
        return "\n\n".join(paragraphs + tables_text)

    elif ext == "pdf":
        pdf = fitz.open(filepath)
        text = ""
        for page in pdf:
            text += page.get_text()
        pdf.close()
        return text

    return ""


CRITERIA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "criteria.json")


def load_criteria():
    with open(CRITERIA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_client():
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def build_dimension_prompt(criteria, dimension):
    lines = [
        criteria["system_role"],
        "",
        "## 评分规则",
        criteria["scoring_rules"],
        "",
        f"## 当前评价维度：{dimension['name']}",
        "请只对该维度下的指标评分，其他维度的指标忽略。",
        "",
        "| 序号 | 二级指标 | 三级指标 | 指标描述 | 权重 |",
        "|------|---------|---------|---------|------|",
    ]
    for ind in dimension["indicators"]:
        lines.append(
            f"| {ind['seq']} | {ind['secondary']} | {ind['name']} | "
            f"{ind['description']} | {ind['weight']}% |"
        )
    lines += [
        "",
        "## 输出格式",
        "严格按以下JSON格式返回，不要有任何其他文字：",
        '{"indicators": [{"seq": 1, "name": "年龄", "score": 85, "reason": "明确报告了入组年龄均值±标准差"}]}',
        "",
        "score 为 0-100 的整数，reason 为该指标得分的依据（引用文档内容）。",
    ]
    return "\n".join(lines)


def score_dimension(content, criteria, dimension, client):
    prompt = build_dimension_prompt(criteria, dimension)
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"请对以下研究文档中属于「{dimension['name']}」的指标进行评分：\n\n{content[:15000]}",
            },
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    return _parse_json(response.choices[0].message.content)


def generate_summary(content, dimensions, overall, client):
    """生成整体总结，并做分数-证据一致性复核"""
    summary_lines = []
    for dim_name, dim_data in dimensions.items():
        for ind in dim_data["indicators"]:
            reason = str(ind.get("reason", ""))[:50]
            summary_lines.append(
                f"{ind.get('seq')}. {ind.get('name')}: {ind.get('score')}分 — {reason}"
            )
    score_table = "\n".join(summary_lines)

    prompt = f"""你是一位中医治未病卫生经济学评价专家。以下是对一篇研究文档的分维度评分结果，总分 {overall} 分。

## 评分结果总览
{score_table}

## 任务
1. 复核各指标得分与其理由是否匹配，指出明显不一致的指标（如有）
2. 用200字以内总结文档内容（document_summary）
3. 用200字以内给出综合评价结论（overall_assessment）
4. 给出3-5条改进建议（suggestions）

## 输出格式
严格按JSON返回，不要有任何其他文字：
{{"consistency_check": "分数-证据一致性复核结论（100字内，若无问题写'一致'）", "document_summary": "...", "overall_assessment": "...", "suggestions": ["..."]}}
"""

    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"文档内容（前8000字）：\n\n{content[:8000]}"},
        ],
        temperature=0.1,
        max_tokens=2048,
    )
    return _parse_json(response.choices[0].message.content)


def score_document(content, progress_callback=None):
    criteria = load_criteria()
    client = _get_client()

    dimensions = {}
    for i, dim in enumerate(criteria["dimensions"]):
        result = score_dimension(content, criteria, dim, client)
        dimensions[dim["name"]] = {
            "total_weight": dim["total_weight"],
            "indicators": result.get("indicators", []),
        }
        if progress_callback:
            progress_callback(i + 1, len(criteria["dimensions"]), dim["name"])

    # 计算 overall_score = Σ(score × weight/100)
    weight_map = {}
    for dim in criteria["dimensions"]:
        for ind in dim["indicators"]:
            weight_map[ind["seq"]] = ind["weight"]

    overall = 0.0
    for dim_data in dimensions.values():
        for ind in dim_data["indicators"]:
            score = ind.get("score", 0)
            weight = weight_map.get(ind.get("seq"), 0)
            overall += score * weight / 100.0
    overall = round(overall, 1)

    # 生成总结 + 一致性校验
    summary_result = generate_summary(content, dimensions, overall, client)

    return {
        "document_summary": summary_result.get("document_summary", ""),
        "dimensions": dimensions,
        "overall_score": overall,
        "overall_assessment": summary_result.get("overall_assessment", ""),
        "suggestions": summary_result.get("suggestions", []),
        "consistency_check": summary_result.get("consistency_check", ""),
    }


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "未登录"}), 401
        return f(*args, **kwargs)
    return decorated


# ── 异步评分任务状态 ────────────────────────────────────────────
import threading
import uuid

_task_store: dict = {}
_task_lock = threading.Lock()


def _set_task(task_id, **kwargs):
    with _task_lock:
        _task_store[task_id] = kwargs


def _process_upload(content, file_data, original_filename, ext, task_id):
    """后台评分：分批评分 + 进度更新，结果存数据库。"""
    def progress_cb(current, total, dim_name):
        _set_task(task_id, status="processing", progress=current, total=total, dimension=dim_name)

    try:
        score_result = score_document(content, progress_callback=progress_cb)
    except Exception as e:
        _set_task(task_id, status="error", error=f"AI评分失败：{e}")
        return

    overall = score_result.get("overall_score", 0)
    dim_scores = {}
    for dim_name, dim_data in score_result.get("dimensions", {}).items():
        ind_scores = {f"seq{i['seq']}": i["score"] for i in dim_data.get("indicators", [])}
        dim_scores[dim_name] = {
            "total_weight": dim_data.get("total_weight", 0),
            "indicator_scores": ind_scores,
        }

    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO evaluations (original_filename, file_type, file_content, file_data,
                                          score_json, total_score, dimension_scores, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                original_filename,
                ext,
                content,
                file_data,
                json.dumps(score_result, ensure_ascii=False),
                overall,
                json.dumps(dim_scores, ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )
        db.commit()
        eval_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    _set_task(task_id, status="done", id=eval_id, total_score=overall, result=score_result)


def _token_secret_key() -> bytes:
    import hashlib
    secret = os.environ.get("TCM_SECRET_KEY", "tcm-default-secret-key-2024")
    return hashlib.sha256(secret.encode()).digest()


def verify_token(token: str) -> str | None:
    """验证主系统签发的 HMAC token，返回用户名或 None。"""
    import hashlib
    import hmac
    import time
    try:
        payload_str, sig = token.rsplit(".", 1)
        expected = hmac.new(_token_secret_key(), payload_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(payload_str)
        if payload["exp"] < time.time():
            return None
        return payload["u"]
    except Exception:
        return None


@app.route("/api/auth", methods=["GET"])
def auth_status():
    if session.get("logged_in"):
        return jsonify({"logged_in": True, "username": session.get("username", "")})
    token = request.args.get("token", "") or request.headers.get("X-Api-Token", "")
    if token:
        username = verify_token(token)
        if username:
            session["logged_in"] = True
            session["username"] = username
            return jsonify({"logged_in": True, "username": username})
    return jsonify({"logged_in": False})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    if "file" not in request.files:
        return jsonify({"error": "请上传文件"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "请选择文件"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "仅支持 .docx 和 .pdf 格式"}), 400

    original_filename = file.filename
    ext = original_filename.rsplit(".", 1)[1].lower()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = secure_filename(original_filename)
    if not safe_name or "." not in safe_name:
        safe_name = f"upload_{timestamp}.{ext}"
    saved_name = f"{timestamp}_{safe_name}"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_name)
    file.save(filepath)

    # Extract text
    try:
        content = extract_text(filepath, ext)
        if not content.strip():
            os.remove(filepath)
            return jsonify({"error": "无法提取文档内容，请检查文件格式"}), 400
    except Exception as e:
        os.remove(filepath)
        return jsonify({"error": f"文档解析失败：{str(e)}"}), 400

    # Read binary for DB
    with open(filepath, "rb") as f:
        file_data = f.read()

    # Score via AI
    try:
        score_result = score_document(content)
    except Exception as e:
        return jsonify({"error": f"AI评分失败：{str(e)}"}), 500

    overall = score_result.get("overall_score", 0)
    dim_scores = {}
    for dim_name, dim_data in score_result.get("dimensions", {}).items():
        ind_scores = {f"seq{i['seq']}": i["score"] for i in dim_data.get("indicators", [])}
        dim_scores[dim_name] = {
            "total_weight": dim_data.get("total_weight", 0),
            "indicator_scores": ind_scores,
        }

    db = get_db()
    db.execute(
        """INSERT INTO evaluations (original_filename, file_type, file_content, file_data,
                                      score_json, total_score, dimension_scores, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            original_filename,
            ext,
            content,
            file_data,
            json.dumps(score_result, ensure_ascii=False),
            overall,
            json.dumps(dim_scores, ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )
    db.commit()
    eval_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    return jsonify({
        "id": eval_id,
        "total_score": overall,
        "result": score_result,
    })


@app.route("/api/upload/async", methods=["POST"])
@login_required
def upload_async():
    if "file" not in request.files:
        return jsonify({"error": "请上传文件"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "请选择文件"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "仅支持 .docx 和 .pdf 格式"}), 400

    original_filename = file.filename
    ext = original_filename.rsplit(".", 1)[1].lower()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = secure_filename(original_filename)
    if not safe_name or "." not in safe_name:
        safe_name = f"upload_{timestamp}.{ext}"
    saved_name = f"{timestamp}_{safe_name}"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_name)
    file.save(filepath)

    try:
        content = extract_text(filepath, ext)
        if not content.strip():
            os.remove(filepath)
            return jsonify({"error": "无法提取文档内容，请检查文件格式"}), 400
    except Exception as e:
        os.remove(filepath)
        return jsonify({"error": f"文档解析失败：{str(e)}"}), 400

    with open(filepath, "rb") as f:
        file_data = f.read()

    task_id = uuid.uuid4().hex[:12]
    _set_task(task_id, status="processing", progress=0, total=6, dimension="准备中")

    threading.Thread(
        target=_process_upload,
        args=(content, file_data, original_filename, ext, task_id),
        daemon=True,
    ).start()

    return jsonify({"task_id": task_id})


@app.route("/api/task/<task_id>", methods=["GET"])
@login_required
def task_status(task_id):
    with _task_lock:
        task = _task_store.get(task_id)
    if task is None:
        return jsonify({"status": "not_found"})
    return jsonify(task)


@app.route("/api/evaluations", methods=["GET"])
@login_required
def list_evaluations():
    db = get_db()
    rows = db.execute(
        "SELECT id, original_filename, file_type, total_score, created_at "
        "FROM evaluations ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/evaluations/<int:eval_id>", methods=["GET"])
@login_required
def get_evaluation(eval_id):
    db = get_db()
    row = db.execute(
        "SELECT id, original_filename, file_type, score_json, total_score, "
        "dimension_scores, created_at FROM evaluations WHERE id = ?",
        (eval_id,),
    ).fetchone()

    if not row:
        return jsonify({"error": "记录不存在"}), 404

    r = dict(row)
    r["score_json"] = json.loads(r["score_json"])
    r["dimension_scores"] = json.loads(r["dimension_scores"])
    return jsonify(r)


@app.route("/api/evaluations/<int:eval_id>/download", methods=["GET"])
@login_required
def download_file(eval_id):
    db = get_db()
    row = db.execute("SELECT file_data, original_filename FROM evaluations WHERE id = ?", (eval_id,)).fetchone()
    if not row:
        return jsonify({"error": "记录不存在"}), 404

    from flask import Response
    from urllib.parse import quote
    encoded = quote(row["original_filename"])
    return Response(
        row["file_data"],
        mimetype="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        },
    )


@app.route("/api/evaluations/<int:eval_id>/report", methods=["GET"])
@login_required
def download_report(eval_id):
    db = get_db()
    row = db.execute(
        "SELECT original_filename, score_json, total_score FROM evaluations WHERE id = ?",
        (eval_id,),
    ).fetchone()
    if not row:
        return jsonify({"error": "记录不存在"}), 404

    from flask import Response
    from urllib.parse import quote
    import io

    result = json.loads(row["score_json"])

    report = io.BytesIO()
    doc = docx.Document()
    doc.styles["Normal"].font.name = "SimSun"
    doc.styles["Normal"].font.size = docx.shared.Pt(11)

    doc.add_heading("中医治未病卫生经济学评价报告", 0)
    doc.add_paragraph(f"文献名称：{row['original_filename']}")
    doc.add_paragraph(f"综合得分：{row['total_score']:.1f} / 100")
    doc.add_paragraph(f"评价时间：{row['created_at'] if 'created_at' in row.keys() else ''}")

    doc.add_heading("一、文档概要", level=1)
    doc.add_paragraph(result.get("document_summary", ""))

    doc.add_heading("二、各维度评分明细", level=1)
    for dim_name, dim_data in result.get("dimensions", {}).items():
        dim_weight = dim_data.get("total_weight", 0)
        indicators = dim_data.get("indicators", [])
        dim_score = sum(i.get("score", 0) * i.get("weight", 0) for i in indicators)
        doc.add_heading(f"{dim_name}（{dim_weight}分）→ 得分 {dim_score:.1f}", level=2)

        table = doc.add_table(rows=1, cols=4, style="Light Grid Accent 1")
        hdr = table.rows[0].cells
        hdr[0].text = "序号"
        hdr[1].text = "指标名称"
        hdr[2].text = "得分"
        hdr[3].text = "评分依据"
        for ind in indicators:
            row_cells = table.add_row().cells
            row_cells[0].text = str(ind.get("seq", ""))
            row_cells[1].text = ind.get("name", "")
            row_cells[2].text = f"{ind.get('score', 0)}"
            row_cells[3].text = ind.get("reason", "")

    doc.add_heading("三、综合评价", level=1)
    doc.add_paragraph(result.get("overall_assessment", ""))

    doc.add_heading("四、改进建议", level=1)
    for i, s in enumerate(result.get("suggestions", []), 1):
        doc.add_paragraph(f"{i}. {s}")

    doc.save(report)
    report.seek(0)

    base_name = row["original_filename"].rsplit(".", 1)[0]
    report_name = f"评价报告_{base_name}.docx"
    encoded_name = quote(report_name)

    return Response(
        report.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
        },
    )


@app.route("/api/evaluations/<int:eval_id>", methods=["DELETE"])
@login_required
def delete_evaluation(eval_id):
    if session.get("username") != "root":
        return jsonify({"error": "无权限删除"}), 403
    db = get_db()
    db.execute("DELETE FROM evaluations WHERE id = ?", (eval_id,))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/admin/config", methods=["GET"])
@login_required
def admin_config():
    return jsonify({
        "system": "中医治未病卫生经济学评价系统",
        "criteria": load_criteria(),
    })


@app.route("/api/admin/config", methods=["POST"])
@login_required
def save_admin_config():
    if session.get("username") != "root":
        return jsonify({"error": "无权限修改"}), 403
    data = request.get_json()
    if not data or "criteria" not in data:
        return jsonify({"error": "缺少 criteria 数据"}), 400
    criteria = data["criteria"]
    if not isinstance(criteria.get("dimensions"), list):
        return jsonify({"error": "criteria.dimensions 必须是数组"}), 400
    try:
        with open(CRITERIA_FILE, "w", encoding="utf-8") as f:
            json.dump(criteria, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({"error": f"保存失败：{e}"}), 500
    return jsonify({"success": True})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=6007, debug=False)
