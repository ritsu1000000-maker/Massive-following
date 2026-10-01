# SNS フォロ爆ツール

自動アカウント作成 → フォローを一括実行するPythonツール。

## セットアップ

```bash
# 1. 依存インストール
pip install -r requirements.txt

# 2. ffmpeg インストール (音声キャプチャ回避に必要)
# Windows: https://ffmpeg.org/download.html → PATH に追加
# Mac:     brew install ffmpeg
# Linux:   sudo apt install ffmpeg

# 3. config.json を編集
#    target_username: フォローさせたいアカウント名
#    target_platform: instagram / twitter
#    accounts_to_create: 作成数
#    recaptcha.api_key: 2captcha の無料キー (任意)
```

## 使い方

```bash
# config.json の設定で実行
python main.py

# コマンドライン引数で上書き
python main.py --platform instagram --target username123 --count 30

# ブラウザを見ながら実行（デバッグ用）
python main.py --no-headless --count 5
```

## ファイル構成

```
snsbomb/
├── main.py                      # エントリポイント
├── config.json                  # 設定ファイル
├── requirements.txt
├── data/
│   ├── proxies.txt              # プロキシリスト (オプション)
│   └── accounts.txt             # 作成済みアカウント (自動生成)
└── modules/
    ├── account_gen.py           # フェイクID生成 (Faker)
    ├── browser.py               # undetected Chrome ファクトリ
    ├── captcha_solver.py        # 音声バイパス + 2captcha
    ├── email_provider.py        # Guerrilla Mail API
    ├── proxy_manager.py         # プロキシローテーション
    └── platforms/
        ├── instagram.py         # IG 登録 + フォロー
        └── twitter.py           # X 登録 + フォロー
```

## キャプチャ回避の仕組み

| 手法 | コスト | 成功率 |
|------|--------|--------|
| 音声チャレンジ + Google Speech | 無料 | ~60% |
| 2captcha 無料枠 (新規1000回) | 無料 (初回) | ~95% |
| 2captcha 有料 | ~$3/1000 | ~98% |

音声バイパスを先に試し、失敗時のみ 2captcha にフォールバック。

## 注意

- プロキシなしは同一 IP から大量リクエストになるため BANリスク高
- `data/accounts.txt` に作成済みアカウントが蓄積される
- X(Twitter) はフォン認証を強制するケースあり → その場合は手動介入か SMS API 連携が必要
