# ロコンドMDS CSV ジェネレーター

futureshop と NextEngine のデータから、ロコンドの商品一括登録用CSV（MDS）を作る Streamlit アプリです。

## 起動

```bash
pip install -r requirements.txt
cd locondo-csv-generator   # このフォルダで起動すると .streamlit/config.toml が読まれます
streamlit run app.py
```

## 入力ファイル

| 欄 | ファイル | 例 |
|---|---|---|
| 1 | FS バリエーション詳細登録用ファイル | `goodsVariationDetail_*.csv` |
| 2 | FS 商品一括登録用ファイル | `goods_*.csv` |
| 3 | NE 商品マスタ全件ダウンロード（zipのままでOK） | `SyohinList.zip` |
| 4 | ロコンド出品中データ（任意） | `commodity_*.csv` |

## マニュアル

`static/manual.pdf` を置くと、画面上部に「マニュアルを開く」ボタンが表示されます。

## ブランド判定

ロコンド出品中データ → 例外品番（画面から登録） → NE商品タグ（[CV][BG][CVR] / [CO][BC] / [BR] / [nm] / [ev]） → 商品名 の順で判定します。
