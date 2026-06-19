import streamlit as st
import pandas as pd
from generator import generate_locondo_csv, GeneratorResult

st.set_page_config(page_title="ロコンドMDS CSVジェネレーター", layout="wide")
st.title("ロコンドMDS CSV ジェネレーター")

# STEP 1: ファイルアップロード
st.header("STEP 1｜ファイルアップロード")
col1, col2, col3 = st.columns(3)
with col1:
    fs_var_files = st.file_uploader("1. FS バリエーションCSV（複数可）", type="csv", key="fs_var", accept_multiple_files=True)
with col2:
    fs_goods_files = st.file_uploader("2. FS 商品情報CSV（複数可）", type="csv", key="fs_goods", accept_multiple_files=True)
with col3:
    ne_file = st.file_uploader("3. NE 商品マスターCSV", type="csv", key="ne")

# STEP 2: オプション
st.header("STEP 2｜オプション")
season_override = st.text_input(
    "シーズン指定",
    placeholder="例: SS2026 （空欄の場合は空になります）"
)

# STEP 3: 実行
st.header("STEP 3｜実行")
if st.button("CSV生成", type="primary", disabled=not (fs_var_files and fs_goods_files and ne_file)):
    with st.spinner("処理中..."):
        try:
            result: GeneratorResult = generate_locondo_csv(
                fs_var_file=fs_var_files,
                ne_file=ne_file,
                fs_goods_file=fs_goods_files,
                season_override=season_override or None,
            )
            st.success(f"✅ {result.success_count}件 生成完了")
            if result.skip_count > 0:
                st.warning(f"⚠️ {result.skip_count}件 スキップ（NEマッチなし）")
            if result.brand_missing:
                st.warning(f"⚠️ ブランド判別不可の品番（手動修正推奨）: {', '.join(result.brand_missing)}")

            st.subheader("プレビュー（先頭10行）")
            st.dataframe(result.df.head(10), use_container_width=True)

            # ダウンロードボタン
            csv_bytes = result.df.fillna('').to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 locondo_mds.csv をダウンロード",
                data=csv_bytes,
                file_name="locondo_mds.csv",
                mime="text/csv",
            )
        except Exception as e:
            st.error(f"エラーが発生しました: {e}")