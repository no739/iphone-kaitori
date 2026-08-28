# iPhone 買取価格比較

新品未開封iPhoneの買取価格を20社横断で自動収集し、Apple風の比較サイトとDiscord通知で見られるようにするプロジェクト。

- **巡回**: 毎日 11:00 / 13:00 / 15:00 / 17:00 / 19:00 (JST) に GitHub Actions が各社サイトから価格取得
- **通知**: 価格変更があった時だけDiscordに速報。毎朝11時に全機種の最高値一覧+前日比
- **サイト**: GitHub Pages (`docs/`) で公開。緑セル=行内最高値、▲▼=前回巡回比

## 初回セットアップ(1回だけ)

1. GitHubに **公開リポジトリ** を作成し、このフォルダをpush
   ```bash
   git remote add origin https://github.com/<ユーザー名>/iphone-kaitori.git
   git push -u origin main
   ```
2. リポジトリの **Settings → Pages** → 「Deploy from a branch」→ Branch: `main` / フォルダ: `/docs` → Save
   - 数分後 `https://<ユーザー名>.github.io/iphone-kaitori/` でサイトが見られる
3. **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `DISCORD_WEBHOOKS`
   - Value: Discord Webhook URL(複数人に送るなら**1行1URL**で改行区切り)
4. **Actions タブ**を開いてワークフローを有効化(初回は「価格チェック」を手動Run推奨)

### Discord Webhook の作り方(家族・友人向け)

1. 通知を受け取りたいDiscordチャンネルで「チャンネルの編集 → 連携サービス → ウェブフック → 新しいウェブフック」
2. 「ウェブフックURLをコピー」
3. そのURLをリポジトリ管理者に渡して `DISCORD_WEBHOOKS` に1行追加してもらう

ローカル実行時は、リポジトリ直下に `webhooks.txt`(1行1URL、gitには入らない)でもOK。

## 新しいiPhoneが出たら

[config/models.json](config/models.json) にシリーズを1ブロック追記するだけ。

```json
{
  "id": "18pro",
  "name": "iPhone 18 Pro",
  "colors": { "orange": "オレンジ", "blue": "ブルー", "silver": "シルバー" },
  "capacities": { "256": 189800, "512": 224800, "1024": 259800 }
}
```

- `capacities` のキーはGB(1TB=1024, 2TB=2048)、値はApple税込定価
- カラー別に価格差が出ない機種(無印/Air系)は `"colors": {}` にするとカラー集約で追跡

## 対象業者

[config/shops.json](config/shops.json) 参照(20社)。`"enabled": false` で一時停止できる。

**対象外**(サイトに価格が載っていない業者):
- 買取当番・毎日買取 — LINE/電話での問い合わせ制

**買取エノキング**は価格表示に会員ログインが必要。Secretsに
`ENOKING_EMAIL` / `ENOKING_PASSWORD`(会員登録したメール/パスワード)を
登録すると自動ログインして取得する。未設定の間は静かにスキップされる。
ローカルでは `enoking_login.txt`(1行目メール、2行目パスワード、git対象外)でも可。

## ローカルでの動作確認

```bash
pip3 install -r requirements.txt
python3 -m scraper.runner kaikyo   # 1店舗だけテスト
python3 -m scraper.main --dry      # 全店舗、通知・保存なし
python3 -m scraper.main            # 本番(webhooks.txt があれば通知)
cd docs && python3 -m http.server 8642   # サイト確認 → http://localhost:8642
```

## 構成

```
scraper/
  shops.py      各社のパーサ(1社=1関数、@shop("id") で登録)
  normalize.py  商品名 → 機種/容量/カラー の正規化
  main.py       巡回・変更検知・通知・保存
  discord.py    Webhook送信
config/
  models.json   追跡する機種マスタ(新機種はここに追記)
  shops.json    業者マスタ
docs/           GitHub Pagesで公開されるサイト(index.html + data/*.json)
.github/workflows/check.yml  定期実行(JST 11-19時の2時間おき)
```

### 業者サイトの構造が変わったら

該当業者の取得が失敗するとDiscordに⚠️通知が来る(サイトの列にも⚠️表示)。
`scraper/shops.py` の該当関数を直せば復旧。失敗中は前回価格を表示し続ける。
