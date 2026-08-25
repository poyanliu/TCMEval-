"""Benchmark PDF parsing and evaluation speed using the same backend as streamlit_app.py."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.utils.document_parser import parse_document
from backend.services.llm_client import load_model, clear_cache
from backend.services.evaluation_service import evaluate_document
from shared.constants import MAX_DOC_CHARS

PDF_DIR = "/root/instructions"
PDF_FILES = [
    "关于开展紧密型城市医疗集团建设试点工作的通知.pdf",
    "关于全面推进紧密型县域医疗卫生共同体建设的指导意见.pdf",
    "关于印发紧密型城市医疗集团试点城市名单的通知.pdf",
]

def format_time(s: float) -> str:
    if s < 1:
        return f"{s*1000:.0f} ms"
    elif s < 60:
        return f"{s:.2f} s"
    else:
        return f"{s/60:.1f} min"

def main():
    print("=" * 70)
    print("  中医药政策文献智能评价系统 — PDF 批量加载性能测试")
    print("=" * 70)

    # ── Phase 1: PDF Parsing ──────────────────────────────────────
    print("\n[Phase 1] PDF 文档解析测试")
    print("-" * 50)
    parsed = {}
    parse_times = {}
    total_parse_start = time.time()

    for i, fname in enumerate(PDF_FILES):
        fpath = os.path.join(PDF_DIR, fname)
        fsize_mb = os.path.getsize(fpath) / 1024 / 1024

        t0 = time.perf_counter()
        try:
            with open(fpath, "rb") as fh:
                doc_name, doc_text = parse_document(fh, fname)
            t1 = time.perf_counter()
            elapsed = t1 - t0
            char_count = len(doc_text)
            parsed[fname] = (doc_name, doc_text)
            parse_times[fname] = elapsed
            print(f"  [{i+1}/3] {fname}")
            print(f"       大小: {fsize_mb:.1f} MB | 字符数: {char_count} | 耗时: {format_time(elapsed)}")
            if char_count < 100:
                print(f"       [WARN] 文本内容较少 ({char_count} chars)，可能为图片型 PDF")
                print(f"       前200字符: {doc_text[:200]}")
        except Exception as e:
            t1 = time.perf_counter()
            parse_times[fname] = t1 - t0
            print(f"  [{i+1}/3] {fname} — FAILED: {e}")

    total_parse = time.time() - total_parse_start
    print(f"\n  解析阶段累计用时: {format_time(total_parse)}")
    if parse_times:
        avg_parse = sum(parse_times.values()) / len(parse_times)
        print(f"  平均单文件解析: {format_time(avg_parse)}")

    # ── Phase 2: Model Loading ────────────────────────────────────
    print(f"\n[Phase 2] 模型加载测试")
    print("-" * 50)
    t0 = time.perf_counter()
    try:
        load_model()
        t1 = time.perf_counter()
        print(f"  模型加载耗时: {format_time(t1 - t0)}")
        model_loaded = True
    except Exception as e:
        t1 = time.perf_counter()
        print(f"  模型加载失败: {e} (耗时: {format_time(t1 - t0)})")
        model_loaded = False

    # ── Phase 3: Single Evaluation ────────────────────────────────
    if model_loaded and parsed:
        print(f"\n[Phase 3] 单文件评价测试 (仅测试第一个文件)")
        print("-" * 50)
        fname = PDF_FILES[0]
        doc_name, doc_text = parsed[fname]

        t0 = time.perf_counter()
        try:
            response = evaluate_document(
                text=doc_text,
                doc_name=doc_name,
                max_chars=MAX_DOC_CHARS,
                include_additional=True,
            )
            t1 = time.perf_counter()
            elapsed = t1 - t0
            print(f"  文件: {doc_name}")
            print(f"  总分: {response.total_score}/100 (基础分: {response.base_score})")
            print(f"  评价耗时: {format_time(elapsed)}")
            print(f"  综合评价: {response.overall_comment[:120]}...")
        except Exception as e:
            t1 = time.perf_counter()
            print(f"  评价失败: {e} (耗时: {format_time(t1 - t0)})")

    # ── Summary ───────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"  测试总结")
    print(f"{'=' * 70}")
    print(f"  解析阶段累计用时: {format_time(total_parse)}")
    print(f"  总文件数: {len(PDF_FILES)}")
    print(f"  总数据量: {sum(os.path.getsize(os.path.join(PDF_DIR, f))/1024/1024 for f in PDF_FILES):.1f} MB")
    if parse_times:
        print(f"  解析最快: {format_time(min(parse_times.values()))}")
        print(f"  解析最慢: {format_time(max(parse_times.values()))}")
    print(f"{'=' * 70}")

if __name__ == "__main__":
    main()
