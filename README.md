# 論文PDF翻訳

英語の論文PDFの本文を日本語に翻訳し、**元のレイアウトのまま本文を上書きした PDF** を作るソフトです。
図・表・数式・化学式・参考文献などはそのまま残し、本文の段落だけを日本語に置き換えます。

翻訳エンジンは 2 種類から選べます。

| エンジン | 料金 | 特徴 |
|---|---|---|
| **Claude** | Claude の有料プラン（Pro / Max など）の利用枠内。追加料金なし | 専門用語の訳が自然。訳すべき段落を AI が判断する |
| **DeepL** | DeepL API Free なら月 50 万文字まで無料（論文数十本分） | Claude の契約がなくても使える。APIキーの登録が必要 |

## 動作環境

- Windows 10 / 11
- Python 3.10 以上
- 次のどちらか
  - Claude Code CLI（Claude の有料プランでログイン済み）
  - DeepL API の APIキー

## インストール

### 1. Python

[python.org](https://www.python.org/downloads/) からインストールします。
インストール画面で **「Add python.exe to PATH」にチェック** を入れてください。

### 2. 必要なライブラリ

このフォルダで PowerShell を開き、次を実行します。

```powershell
pip install -r requirements.txt
```

### 3-A. Claude を使う場合：Claude Code CLI

PowerShell で次を実行します（Anthropic 公式のインストーラ）。

```powershell
irm https://claude.ai/install.ps1 | iex
```

終わったら PowerShell を開き直し、ログインします。

```powershell
claude
```

> `claude` が見つからないと言われた場合は、代わりに
> `& "$env:USERPROFILE\.local\bin\claude.exe"` を実行してください。
> （このソフトはその場所も自動で探すので、PATH が通っていなくても動きます）

1. 文字色のテーマを選ぶ（どれでも可）
2. ログイン方法は **「Claude account with subscription」** を選ぶ
   （「Anthropic Console account」は API の従量課金になるので選ばない）
3. ブラウザが開くので、自分の Claude アカウントでログインして承認する
4. PowerShell に戻ったら `/exit` で終了する

次のコマンドで日本語の返事が返ってくれば準備完了です。

```powershell
claude -p "こんにちは"
```

### 3-B. DeepL を使う場合：APIキーの取得

1. [DeepL API のページ](https://www.deepl.com/ja/pro-api) を開き、**「DeepL API Free」** に登録する
   （本人確認のためクレジットカードの登録が必要ですが、Free プランでは請求されません）
2. アカウントページの「API キー」から、キーをコピーする（末尾が `:fx` のもの）
3. ソフトの画面で翻訳エンジンを「DeepL」にし、APIキー欄に貼り付ける

## 使い方

### 画面版（おすすめ）

`論文翻訳.pyw` をダブルクリックして起動します。

1. 上の枠に論文 PDF をドラッグ＆ドロップ（クリックや「ファイルを追加…」でも選べます。複数可）
2. 設定を選ぶ
   - **翻訳エンジン**：Claude ／ DeepL
   - **モデル**（Claude のとき）：Sonnet（標準）／ Opus（高品質・遅い）／ Haiku（高速）
   - **APIキー**（DeepL のとき）
   - **保存先**：元の PDF と同じフォルダ、または指定したフォルダ
3. 「翻訳開始」を押す
4. 終わったら一覧の行をダブルクリック（または「訳したPDFを開く」）で結果を開く

出力ファイル名は `元のファイル名_日本語訳.pdf` です。
6 ページ程度の論文で、Claude なら 1〜2 分、DeepL なら数十秒かかります。

設定（エンジン・モデル・APIキー・保存先）は `論文翻訳_設定.json` に保存され、次回も引き継がれます。
**このファイルには DeepL の APIキーが入るので、他人に渡さないでください。**

### ドラッグ＆ドロップだけで使う

`PDFを翻訳.bat` に PDF をドラッグ＆ドロップすると、黒い画面で翻訳が進み、元の PDF と同じフォルダに訳した PDF ができます（Claude を使います）。

### コマンドライン

```powershell
python translate_pdf.py 論文.pdf
python translate_pdf.py 論文.pdf -o 出力先.pdf --model opus
python translate_pdf.py 論文.pdf --engine deepl --deepl-key あなたのキー
```

DeepL のキーは、環境変数 `DEEPL_API_KEY` に設定しておくこともできます。

## できないこと・注意点

- **スキャンした画像だけの PDF**（文字を選択できない PDF）には対応していません
- 図・表の中の文字、数式、参考文献リストは翻訳しません
- DeepL のときは、本文かどうかを文字の大きさや数字の割合で機械的に判定するため、
  見出しや図の説明が訳されなかったり、逆に訳されたりすることがあります
- 段落が段組みやページをまたぐ場合、区切りごとに別々に訳すため、つなぎ目の訳が少し不自然になることがあります
- 日本語は英語より長くなりやすいため、枠に収まらない段落は文字が小さくなります
- 訳した PDF は個人の学習・研究用にとどめ、論文の著作権に注意してください

## ファイル構成

| ファイル | 内容 |
|---|---|
| `論文翻訳.pyw` | 画面版（ダブルクリックで起動） |
| `PDFを翻訳.bat` | ドラッグ＆ドロップ用 |
| `translate_pdf.py` | 翻訳・上書き処理の本体（コマンドラインでも使える） |
| `requirements.txt` | 必要な Python ライブラリ |
| `LICENSE` | ライセンス（MIT） |

## ライセンス

[MIT License](LICENSE) です。
個人・商用を問わず、自由に使用・改変・再配布できます。再配布するときは、著作権表示と LICENSE の全文を含めてください。
このソフトは無保証です。

---

<p align="right"><sub>Product by <b>shiito</b></sub></p>
