"""論文PDFの本文を日本語に翻訳し、元の位置に上書きしたPDFを作るツール。

翻訳エンジン:
    claude : Claude Code CLI (`claude -p`) を呼び出す（サブスク枠内・APIキー不要）
    deepl  : DeepL API（Free プランなら月50万文字まで無料・APIキーが必要）

使い方:
    python translate_pdf.py 論文.pdf                      -> 論文_日本語訳.pdf を出力
    python translate_pdf.py 論文.pdf --engine deepl --deepl-key XXXX:fx

Product by shiito
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import fitz  # PyMuPDF

FONT_DIR = r"C:\Windows\Fonts"
FONT_FILE = "yumin.ttf"  # 游明朝
CHUNK_CHARS = 12000  # 1回の claude 呼び出しに渡す英文の目安文字数
ENGINES = ("claude", "deepl")

PROMPT = """あなたは化学・材料系の学術論文の翻訳者です。
以下のJSONは英語論文PDFから取り出したテキストブロックです（キー=ブロックID）。
各ブロックを自然で正確な日本語（である調）に翻訳してください。

ルール:
- 化合物名・試薬名・装置名・単位・数値・化合物番号（太字の 3, 4 など）は原文の表記を保つか、慣用的なカタカナ表記にする。
- <sup>…</sup>（上付き）と <sub>…</sub>（下付き）のタグは、対応する文字に付けたまま訳文に残す。
- 段落が途中で切れているブロックは、切れている箇所までだけを訳す（前後のブロックの内容を補わない）。
- 次のブロックは翻訳せず null にする: 著者名・所属、参考文献リスト、数式や化学式だけのもの、
  NMR等の数値データだけのもの、図表内のラベル、ページ番号や柱（ヘッダ）、短い見出し。
- 出力は {"ブロックID": "訳文" または null, ...} というJSONオブジェクトのみ。説明文やコードブロック記号は付けない。

入力:
"""


# ---------------------------------------------------------------- 抽出

# Elsevier 等の古いPDFは記号を専用フォントで描いており、文字コードがASCIIのままになっている。
# フォント名ごとに本来の文字へ戻す。
GREEK = dict(zip("abgdezhqiklmnxoprstufcywABGDEZHQIKLMNXOPRSTUFCYW",
                 "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"))
SYMBOL_FONTS = [
    ("Greek", GREEK),
    ("MathematicalPi", {"8": "°", "1": "+", "2": "−"}),
    ("AdvMathSymb", {"\x88": "="}),
    ("CommPi", {"q": "©"}),
]


def span_text(span):
    t = span["text"]
    for key, table in SYMBOL_FONTS:
        if key in span["font"]:
            return "".join(table.get(ch, ch) for ch in t)
    return t


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def line_text(line):
    """1行分の文字列。小さい文字で基線がずれているものは <sup>/<sub> で囲む。"""
    spans = [s for s in line["spans"] if s["text"]]
    if not spans:
        return ""
    main = max(spans, key=lambda s: (s["size"], len(s["text"].strip())))
    base, size = main["origin"][1], main["size"]
    out = ""
    for s in spans:
        t = html_escape(span_text(s))
        if t.strip() and s["size"] < size * 0.85:
            dy = s["origin"][1] - base
            if dy < -size * 0.1:
                t = f"<sup>{t.strip()}</sup>"
            elif dy > size * 0.05:
                t = f"<sub>{t.strip()}</sub>"
        out += t
    return out.replace("</sup><sup>", "").replace("</sub><sub>", "")


def block_text(block):
    """PyMuPDF の text block から1つの文字列を作る（行末ハイフンは連結）。"""
    out = ""
    for line in block["lines"]:
        t = line_text(line).strip()
        if not t:
            continue
        if out.endswith("-"):
            out += t
        elif out:
            out += " " + t
        else:
            out = t
    return re.sub(r"\s+", " ", out).strip()


REFERENCES_HEADING = re.compile(
    r"^(\d+\.?\s*)?(references|bibliography|literature cited|references and notes)$", re.I)


def extract_blocks(doc):
    blocks = []
    after_refs = False
    for pno, page in enumerate(doc):
        h = page.rect.height
        for i, b in enumerate(page.get_text("dict")["blocks"]):
            if b["type"] != 0:
                continue
            text = block_text(b)
            rect = fitz.Rect(b["bbox"])
            if REFERENCES_HEADING.match(text):
                after_refs = True
            if len(text.split()) < 4:  # 見出し・番号・ラベル
                continue
            if rect.y1 < h * 0.07 or rect.y0 > h * 0.95:  # 柱・ノンブル
                continue
            spans = [s for l in b["lines"] for s in l["spans"] if s["text"].strip()]
            sizes = [s["size"] for s in spans]
            blocks.append({
                "id": f"p{pno + 1}b{i}",
                "page": pno,
                "rect": list(rect),
                "spans": [list(s["bbox"]) for s in spans],
                "size": max(set(sizes), key=sizes.count) if sizes else 9,
                "italic": all("Italic" in s["font"] or s["flags"] & 2 for s in spans),
                "after_refs": after_refs,
                "text": text,
            })
    return blocks


def looks_like_prose(b, body_size):
    """Claude 以外のエンジン用: 本文の段落らしいブロックだけを選ぶ。

    Claude は訳すべきでないブロックを自分で判断するが、DeepL は何でも訳してしまうため、
    タイトル・著者・所属・見出し・参考文献・数値データなどをここで除く。
    """
    if b["after_refs"] or b["italic"]:
        return False
    if not body_size * 0.85 <= b["size"] <= body_size * 1.2:
        return False
    # 文章の後ろに NMR データが続く段落もあるので、冒頭部分で判定する
    words = re.sub(r"<[^>]+>", "", b["text"]).split()[:40]
    wordy = [w for w in words if re.search(r"[A-Za-z]{3,}", w)]
    return len(wordy) >= 6 and len(wordy) / len(words) >= 0.5


def body_font_size(blocks):
    """文字数で重み付けした最頻の文字サイズ（＝本文の文字サイズ）。"""
    weight = {}
    for b in blocks:
        k = round(b["size"], 1)
        weight[k] = weight.get(k, 0) + len(b["text"])
    return max(weight, key=weight.get) if weight else 10


# ---------------------------------------------------------------- 翻訳

def find_claude():
    exe = shutil.which("claude")
    if exe:
        return exe
    # 公式インストーラの既定の場所（PATH 未反映の場合）
    exe = os.path.expandvars(r"%USERPROFILE%\.local\bin\claude.exe")
    if os.path.exists(exe):
        return exe
    # Claude デスクトップアプリ同梱の CLI（最新版を使う）
    pattern = os.path.expandvars(
        r"%LOCALAPPDATA%\Claude-Data\*\claude-code\*\*\claude.exe")
    found = sorted(glob.glob(pattern), key=os.path.getmtime)
    if found:
        return found[-1]
    sys.exit("claude コマンドが見つかりません。Claude Code をインストールしてください。")


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSONが見つかりません:\n" + text[:500])
    return json.loads(m.group(0))


def call_claude(claude, payload, model):
    cmd = [claude, "-p", "--output-format", "text", "--tools", "",
           "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    prompt = PROMPT + json.dumps(payload, ensure_ascii=False, indent=0)
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                       encoding="utf-8", timeout=900,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(f"claude の実行に失敗しました:\n{r.stdout}\n{r.stderr}")
    return parse_json(r.stdout)


def chunked(blocks):
    chunk, size = [], 0
    for b in blocks:
        if chunk and size + len(b["text"]) > CHUNK_CHARS:
            yield chunk
            chunk, size = [], 0
        chunk.append(b)
        size += len(b["text"])
    if chunk:
        yield chunk


def translate_claude(blocks, model, log=print, progress=None):
    results = {}
    claude = find_claude()
    chunks = list(chunked(blocks))
    for n, chunk in enumerate(chunks, 1):
        log(f"  翻訳中 {n}/{len(chunks)} ({len(chunk)}ブロック)...")
        if progress:
            progress((n - 1) / len(chunks))
        payload = {b["id"]: b["text"] for b in chunk}
        for attempt in range(2):
            try:
                result = call_claude(claude, payload, model)
                break
            except (ValueError, json.JSONDecodeError) as e:
                if attempt:
                    raise
                log(f"  応答の解析に失敗、再試行します ({e.__class__.__name__})")
        for b in chunk:
            results[b["id"]] = result.get(b["id"])
    if progress:
        progress(1.0)
    return results


DEEPL_ERRORS = {
    403: "DeepL の APIキーが正しくありません。",
    456: "DeepL の今月の無料枠（50万文字）を使い切りました。",
}


def deepl_request(key, texts):
    key = key.strip()
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    data = urllib.parse.urlencode(
        [("text", t) for t in texts] + [
            ("source_lang", "EN"), ("target_lang", "JA"),
            ("tag_handling", "html"),  # <sup>/<sub> を保つ
            ("formality", "prefer_less"),  # である調寄り
        ]).encode()
    req = urllib.request.Request(
        f"https://{host}/v2/translate", data=data,
        headers={"Authorization": f"DeepL-Auth-Key {key}"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return [t["text"] for t in json.load(r)["translations"]]
        except urllib.error.HTTPError as e:
            if e.code in DEEPL_ERRORS:
                raise RuntimeError(DEEPL_ERRORS[e.code]) from None
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 2)  # 混雑時は待って再試行
                continue
            raise RuntimeError(f"DeepL でエラーが発生しました (HTTP {e.code}): "
                               f"{e.read().decode(errors='replace')[:200]}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"DeepL に接続できません: {e.reason}") from None


def translate_deepl(blocks, key, log=print, progress=None):
    if not key or not key.strip():
        raise RuntimeError("DeepL の APIキーが設定されていません。")
    body = body_font_size(blocks)
    todo = [b for b in blocks if looks_like_prose(b, body)]
    log(f"  本文と判定した {len(todo)}/{len(blocks)} ブロックを翻訳します")
    results = {b["id"]: None for b in blocks}
    # DeepL は1回50件まで。リクエストが大きくなりすぎないよう文字数でも区切る
    batches, cur, size = [], [], 0
    for b in todo:
        if cur and (len(cur) >= 50 or size + len(b["text"]) > 30000):
            batches.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += len(b["text"])
    if cur:
        batches.append(cur)
    for n, batch in enumerate(batches, 1):
        log(f"  翻訳中 {n}/{len(batches)} ({len(batch)}ブロック)...")
        if progress:
            progress((n - 1) / len(batches))
        for b, t in zip(batch, deepl_request(key, [b["text"] for b in batch])):
            results[b["id"]] = t
    if progress:
        progress(1.0)
    return results


def translate(blocks, engine="claude", model="sonnet", deepl_key=None,
              log=print, progress=None):
    """{ブロックID: 訳文 または None} を返す。"""
    if engine == "deepl":
        return translate_deepl(blocks, deepl_key, log, progress)
    return translate_claude(blocks, model, log, progress)


# ---------------------------------------------------------------- 上書き

def to_html(text):
    """訳文を HTML にする。<sup>/<sub> 以外のタグはエスケープする。"""
    s = html_escape(text)
    s = re.sub(r"&lt;(/?)(sup|sub)&gt;", r"<\1\2>", s)
    # 抽出時にエスケープ済みだった記号が二重になったものを戻す
    s = re.sub(r"&amp;(amp|lt|gt);", r"&\1;", s)
    return s.replace("°C", "℃")


def insert_rect(b, page_blocks):
    """訳文を入れる枠。下に空きがあれば少し広げ、別ブロックに重なる場合はその手前までにする。"""
    r = fitz.Rect(b["rect"])
    r.y1 += b["size"] * 1.5
    for o in page_blocks:
        orect = fitz.Rect(o["bbox"])
        if o["number"] == b["number"] or orect.x1 <= r.x0 or orect.x0 >= r.x1:
            continue
        if r.y0 + 2 < orect.y0 < r.y1:
            r.y1 = orect.y0 - 1
    return r


def overlay(doc, blocks, translations):
    archive = fitz.Archive(FONT_DIR)
    by_page = {}
    for b in blocks:
        if translations.get(b["id"]):
            by_page.setdefault(b["page"], []).append(b)

    for pno, items in by_page.items():
        page = doc[pno]
        page_blocks = page.get_text("dict")["blocks"]
        for b in items:
            b["number"] = int(b["id"].split("b")[1])
            b["insert"] = insert_rect(b, page_blocks)
            # 文字単位で消すため、span の枠を上下少し縮めて隣の行を巻き込まないようにする
            for x0, y0, x1, y1 in b["spans"]:
                pad = (y1 - y0) * 0.2
                page.add_redact_annot(fitz.Rect(x0, y0 + pad, x1, y1 - pad), fill=(1, 1, 1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        for b in items:
            rect = b["insert"]
            css = (f"@font-face {{font-family: jp; src: url({FONT_FILE});}}"
                   f"* {{font-family: jp; font-size: {b['size']:.1f}pt;"
                   " line-height: 1.2; text-align: left; margin: 0;}"
                   "sup, sub {font-size: 70%;}")
            html = f"<p>{to_html(translations[b['id']])}</p>"
            # 収まらない時は自動で縮小される（scale_low=0: 下限なし）
            page.insert_htmlbox(rect, html, css=css, archive=archive, scale_low=0)
    doc.subset_fonts()


# ---------------------------------------------------------------- main

def output_path(src):
    return os.path.splitext(src)[0] + "_日本語訳.pdf"


def process(src, out=None, engine="claude", model="sonnet", deepl_key=None,
            log=print, progress=None):
    """PDF 1つを翻訳して上書きし、出力ファイルのパスを返す。

    progress には 0.0〜1.0 の翻訳の進み具合が渡される。
    """
    src = os.path.abspath(src)
    out = out or output_path(src)

    doc = fitz.open(src)
    blocks = extract_blocks(doc)
    name = "DeepL" if engine == "deepl" else "Claude"
    log(f"{os.path.basename(src)}: {len(doc)}ページ, 翻訳候補 {len(blocks)}ブロック（{name}）")
    translations = translate(blocks, engine, model, deepl_key, log, progress)

    log("  PDFに書き込み中...")
    overlay(doc, blocks, translations)
    doc.save(out, garbage=3, deflate=True)
    log(f"完了: {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description="論文PDFの本文を日本語訳で上書きする")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output")
    ap.add_argument("--engine", choices=ENGINES, default="claude", help="翻訳エンジン")
    ap.add_argument("--model", default="sonnet", help="claude のモデル (sonnet/opus/haiku)")
    ap.add_argument("--deepl-key", default=os.environ.get("DEEPL_API_KEY"),
                    help="DeepL の APIキー（環境変数 DEEPL_API_KEY でも可）")
    args = ap.parse_args()
    try:
        process(args.pdf, args.output, args.engine, args.model, args.deepl_key,
                log=lambda m: print(m, flush=True))
    except RuntimeError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
