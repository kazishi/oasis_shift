# OASIS シフト作成ツール — Windows 移管手順

## 事前準備（移管元 Mac で行う）

1. `oasis_shift` フォルダを ZIP 圧縮する
   - **`venv` フォルダは除外**（大きくて移植不可のため）
   - `__pycache__` フォルダも除外して OK

### コピーするファイル一覧

```
oasis_shift/
├── app.py
├── config.py
├── data_loader.py
├── optimizer.py
├── sheet_operations.py
├── validator.py
├── requirements.txt
├── 起動.bat
└── google key/
    └── oasis-shift-app-473679a9583e.json
```

> ⚠️ `google key/` 内の JSON ファイルは**サービスアカウントの秘密鍵**です。
> USB で持ち運ぶ場合はパスワード付き ZIP に入れてください。メール送信は避けること。

---

## 移管先 Windows PC での手順

### Step 1　Python をインストール

1. https://www.python.org/downloads/ を開く
2. **Python 3.12.x** をダウンロード
3. インストーラーを実行し、**「Add Python to PATH」に必ずチェックを入れる**
4. インストール完了後、コマンドプロンプトで確認

```
python --version
```

`Python 3.12.x` と表示されれば OK。

---

### Step 2　プロジェクトフォルダを配置

ZIP を展開し、`oasis_shift` フォルダを任意の場所に置く（例: `C:\Users\yourname\oasis_shift`）。

---

### Step 3　初回起動

`oasis_shift` フォルダ内の **`起動.bat`** をダブルクリックする。

初回は以下が自動で実行されます：

1. Python 仮想環境（`venv`）の作成
2. 必要なパッケージのインストール（数分かかります）
3. ブラウザでアプリが起動

**2回目以降は `起動.bat` をダブルクリックするだけで即起動します。**

---

## トラブルシューティング

### `python` が認識されない

インストール時に「Add Python to PATH」のチェックを忘れた場合。

→ Python を再インストールしてチェックを入れ直す、または以下で確認：

```
where python
```

### `pip install` でエラーが出る

社内ネットワークのプロキシが原因の場合があります。IT 担当者にプロキシ設定を確認してください。

### ブラウザが自動で開かない

ターミナルに表示される `http://localhost:8501` を手動でブラウザに貼り付けてください。

### 403 PermissionError（Google スプレッドシートにアクセスできない）

サービスアカウントがスプレッドシートの共有メンバーに入っていない場合。

対象スプレッドシートを開き、以下のメールアドレスを **編集者** として共有してください：

```
oasis-shift-app@oasis-shift-app.iam.gserviceaccount.com
```

共有が必要なスプレッドシート：

| 用途 | スプレッドシート |
|------|----------------|
| 職員名簿 | staff |
| 稼働日カレンダー | calendar |
| シフトマスター | master |
| フォーム回答・仮置き | form_responses |
| Output・調整用・シフト希望 | Output SS |

---

## アンインストール方法

`oasis_shift` フォルダごと削除するだけで完全に消えます。
レジストリや他の場所には何も書き込みません。
