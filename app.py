import os
from datetime import date, datetime

import pandas as pd
import streamlit as st

from generator import (
    BRAND_LABELS, BRAND_MAP, GeneratorResult, InputError,
    generate_locondo_csv, load_saved_exceptions, save_exceptions,
)

st.set_page_config(page_title="ロコンドMDS CSVジェネレーター", layout="wide")
st.title("ロコンドMDS CSV ジェネレーター")

# マニュアル（static/manual.pdf があるときだけ表示）
MANUAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "manual.pdf")
if os.path.exists(MANUAL_PATH):
    if st.get_option("server.enableStaticServing"):
        st.link_button("📖 マニュアルを開く（PDF）", "app/static/manual.pdf")
    else:
        # 設定ファイル（.streamlit/config.toml）が読まれていない場合はダウンロードで代用
        with open(MANUAL_PATH, "rb") as f:
            st.download_button("📖 マニュアルをダウンロード（PDF）", f.read(),
                               file_name="manual.pdf", mime="application/pdf")

st.caption("futureshop と NextEngine のデータから、ロコンドの商品一括登録用CSV（MDS）を作ります。"
           "上から順に 1 → 3 の欄を埋めて「CSV生成」を押してください。")

CODE_TO_LABEL = {code: BRAND_LABELS[p] for p, code in BRAND_MAP.items()}
PREFIX_TO_LABEL = {p: f"{label}" for p, label in BRAND_LABELS.items()}
LABEL_TO_PREFIX = {v: k for k, v in PREFIX_TO_LABEL.items()}


def season_options(today: date):
    """今日の日付から選択肢と既定値を作る（8月〜翌1月はAW、2〜7月はSS）"""
    y = today.year
    if today.month >= 8:
        current = f"AW{y}"
    elif today.month == 1:
        current = f"AW{y - 1}"
    else:
        current = f"SS{y}"
    opts = [f"SS{y + 1}", f"AW{y}", f"SS{y}", f"AW{y - 1}", f"SS{y - 1}"]
    return opts, opts.index(current) if current in opts else 1


# ───────── STEP 1 ─────────
st.header("STEP 1｜ファイルをアップロード")
col1, col2, col3 = st.columns(3)
with col1:
    fs_var_files = st.file_uploader(
        "1. FS バリエーションCSV（必須・複数可）", type="csv", key="fs_var", accept_multiple_files=True,
        help="futureshop の商品バリエーションのCSV。ファイル名の例: goodsVariationDetail_20261020123101.csv",
    )
    st.caption("例: `goodsVariationDetail_〜.csv`")
with col2:
    fs_goods_files = st.file_uploader(
        "2. FS 商品情報CSV（必須・複数可）", type="csv", key="fs_goods", accept_multiple_files=True,
        help="futureshop の商品情報のCSV。ファイル名の例: goods_20261020123036.csv",
    )
    st.caption("例: `goods_〜.csv`")
with col3:
    ne_file = st.file_uploader(
        "3. NE 商品マスター（必須）", type=["csv", "zip"], key="ne",
        help="NextEngine の商品マスター。ダウンロードした zip のままで大丈夫です。例: SyohinList.zip",
    )
    st.caption("例: `SyohinList.zip`（解凍不要）")

with st.expander("4. ロコンド出品中データ（任意）"):
    st.write("ロコンドに出品済みの品番は、ロコンドに登録されているブランドに合わせて出力します。"
             "入れなくても NE の商品タグからブランドを判定できるので、通常は不要です。")
    ref_files = st.file_uploader(
        "ロコンド出品中データ", type="csv", key="ref", accept_multiple_files=True,
        help="ロコンドの管理画面からダウンロードした商品一覧。例: commodity_20261020_122117.csv",
        label_visibility="collapsed",
    )

# ───────── STEP 2 ─────────
st.header("STEP 2｜オプション")
opts, default_idx = season_options(date.today())
season = st.selectbox("シーズン（ロコンドの必須項目）", opts, index=default_idx)
exclude_reservation = st.checkbox(
    "予約商品を除外する（FSの商品名・キャッチコピーに「予約」を含む品番）", value=True
)
infer_category = st.checkbox(
    "カテゴリーを商品名から推定する（オフのときは空欄で出力し、ロコンド側でカテゴリーが設定されます）", value=False
)

with st.expander("ブランドの例外品番（自動判定できない品番を登録）"):
    st.write("ブランドが自動で判定できない品番や、判定を上書きしたい品番を登録します。"
             "「保存」を押すと次回以降も使われます。")
    saved = load_saved_exceptions()
    exc_df = pd.DataFrame(
        [{"品番": k, "ブランド": PREFIX_TO_LABEL[v]} for k, v in saved.items()] or [{"品番": "", "ブランド": None}]
    )
    edited = st.data_editor(
        exc_df, num_rows="dynamic", use_container_width=True, key="exc_editor",
        column_config={
            "品番": st.column_config.TextColumn("品番（例: 654130）"),
            "ブランド": st.column_config.SelectboxColumn("ブランド", options=list(LABEL_TO_PREFIX.keys())),
        },
    )
    current_exceptions = {
        str(r["品番"]).strip(): LABEL_TO_PREFIX[r["ブランド"]]
        for _, r in edited.iterrows()
        if str(r.get("品番") or "").strip() and r.get("ブランド") in LABEL_TO_PREFIX
    }
    if st.button("例外品番を保存"):
        save_exceptions(current_exceptions)
        st.success(f"{len(current_exceptions)}件を保存しました。")

# ───────── STEP 3 ─────────
st.header("STEP 3｜CSVを生成")
missing = [label for label, f in [("1. FS バリエーションCSV", fs_var_files),
                                  ("2. FS 商品情報CSV", fs_goods_files),
                                  ("3. NE 商品マスター", ne_file)] if not f]
if missing:
    st.info("次のファイルを入れると「CSV生成」を押せます: " + "、".join(missing))

if st.button("CSV生成", type="primary", disabled=bool(missing)):
    with st.spinner("処理中...（NE商品マスターが大きいと数十秒かかります）"):
        try:
            st.session_state["result"] = generate_locondo_csv(
                fs_var_file=fs_var_files,
                ne_file=ne_file,
                fs_goods_file=fs_goods_files,
                season_override=season,
                locondo_ref_file=ref_files or None,
                infer_category=infer_category,
                extra_exceptions=current_exceptions,
                exclude_reservation=exclude_reservation,
            )
            st.session_state["generated_at"] = datetime.now()
            st.session_state.pop("error", None)
        except InputError as e:
            st.session_state.pop("result", None)
            st.session_state["error"] = str(e)
        except Exception as e:
            st.session_state.pop("result", None)
            st.session_state["error"] = f"想定外のエラーが発生しました: {e}"

if st.session_state.get("error"):
    st.error(st.session_state["error"])

result: GeneratorResult = st.session_state.get("result")
if result is not None:
    df = result.df
    n_hinban = df["品番"].nunique()
    st.success(f"✅ {n_hinban}品番・{result.success_count}SKU を生成しました")

    # 要確認リスト（表）
    issues = []
    def add(kind, items, action):
        for it in items:
            issues.append({"内容": kind, "対象": it, "対処": action})
    add("ブランド判別不可", result.brand_missing, "NEで商品タグを付けるか、上の「例外品番」に登録してください（ブランドコードが空欄で出力されています）")
    add("ブランドの判定元が食い違い", result.brand_conflict, "出品中データ→例外品番→NE商品タグ→商品名 の順で優先して出力しています。正しいか確認してください")
    add("製品種別を判定できず", result.type_uncertain, "「シューズ」として出力しています。バッグの場合は例外として連絡してください")
    add("サイズを変換できず", result.size_invalid, "ロコンドサイズが空欄です。FSのサイズ表記を確認してください")
    add("JANコードが空のため除外", result.jan_missing, "NE/FSでJANを登録すると次回から出力されます")
    add("予約商品のため除外", result.reservation_excluded, "入荷して予約販売が終わったら（FSの商品名・キャッチコピーから「予約」を外したら）出力されます")
    if result.skip_count:
        issues.append({"内容": "NEに商品コードがなくスキップ", "対象": f"{result.skip_count}SKU",
                       "対処": "NE商品マスターが最新か確認してください"})
    if infer_category:
        add("カテゴリー未判定", result.category_missing, "空欄（ロコンドの既定カテゴリ）で出力しています")

    if issues:
        st.subheader(f"⚠️ 確認が必要な項目（{len(issues)}件）")
        st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)
    else:
        st.info("確認が必要な項目はありません。")

    # 品番ごとの確認表
    st.subheader("品番ごとの内容")
    summary = (
        df.assign(ブランド=df["ブランドコード"].map(CODE_TO_LABEL).fillna("（判別不可）"))
          .groupby("品番", sort=False)
          .agg(ブランド=("ブランド", "first"), 製品種別=("製品種別", "first"), 製品名=("製品名", "first"),
               定価税込=("定価（税込）", "first"), 原産国=("原産国", "first"), 靴幅=("靴幅", "first"),
               カラー数=("カラーコード", "nunique"), SKU数=("JANコード", "count"))
          .reset_index()
          .rename(columns={"定価税込": "定価（税込）"})
    )
    st.dataframe(summary, use_container_width=True, hide_index=True)

    with st.expander("出力CSVのプレビュー（全列・先頭20行）"):
        st.dataframe(df.head(20).astype(str), use_container_width=True)

    stamp = st.session_state["generated_at"].strftime("%Y%m%d_%H%M")
    st.download_button(
        label=f"📥 locondo_mds_{stamp}.csv をダウンロード",
        data=df.fillna("").to_csv(index=False).encode("utf-8-sig"),
        file_name=f"locondo_mds_{stamp}.csv",
        mime="text/csv",
        type="primary",
    )
    st.caption("ダウンロードしたCSVを、ロコンドの管理画面からMDS（商品情報一括登録）としてアップロードしてください。"
               "Excelで開いて上書き保存すると、JANコードの桁や文字コードが崩れることがあるのでご注意ください。")
