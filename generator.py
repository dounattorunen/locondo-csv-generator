import pandas as pd
import re
from dataclasses import dataclass, field
from typing import Optional

# ロコンドMDS列順（公式テンプレート完全一致）
MDS_COLUMNS = [
    'カテゴリー1', 'カテゴリーコード1', '製品種別', 'ジェンダー', 'ブランドコード',
    'シーズン', '品番', 'カラーコード', 'カラー', 'ロコンドカラー',
    'ロコンドサイズ', 'サイズ', 'サイズレンジ表記', 'サイズコード', 'JANコード',
    'ハウスコード', '製品名', '定価（税抜き）', '定価（税込）',
    'セール価格（税抜き）', 'セール価格（税込）', '商品説明文', 'マーケットプレイス',
    'カテゴリー2', 'カテゴリーコード2', '原産国', '表素材', '内側素材',
    '中敷素材', 'ソール素材', '靴幅', '素材詳細', '洗濯表示', '寸法詳細',
    '備考', '下代（税抜き）', '価格表記タイプ', 'エラー内容'
]

BRAND_MAP = {
    'CV': 'CO5377', 'NM': 'NE4311', 'BC': 'BA5378', 'BR': 'BA5379', 'EL': 'EL8296'
}

# 全項目共通の禁止ワードリスト（景表法・ロコンドルール対応）
FORBIDDEN_WORDS = [
    r'[0-9０-９]+(?:%|％)(?:OFF|off|オフ)', 
    r'[0-9０-９]+円引', 
    r'セール', r'激安', r'再値下げ', r'送料無料', r'返品不可', r'ネコポス',
    r'大好評', r'最高', r'ベストセラー', r'完全防水', r'治す', r'治療'
]

@dataclass
class GeneratorResult:
    df: pd.DataFrame
    success_count: int
    skip_count: int
    brand_missing: list = field(default_factory=list)

# --- 各種抽出・変換ロジック ---

def get_brand_info(item_code, item_name, description):
    code_str = str(item_code).strip()
    name_str = str(item_name)
    desc_str = str(description) # 商品説明文も判定に使う
    
    # 【追加機能1】例外品番の強制マッピングリスト
    # どうしても判別できない品番は、ここに '品番': ('ブランドコード', 'ブランド略称') の形式で追加してください。
    exceptions = {
        '654130': ('CO5377', 'CV'),  # 654130 は クロールバリエとする場合
        '654131': ('CO5377', 'CV'),  # 654131 も クロールバリエとする場合
        # '別の品番': ('ブランドコード', '略称'),
    }
    if code_str in exceptions:
        return exceptions[code_str]

    # ① まずはURLコードの先頭アルファベットから判定
    match = re.match(r'^([a-zA-Z]+)', code_str)
    if match:
        prefix = match.group(1).upper()
        if prefix in BRAND_MAP:
            return BRAND_MAP[prefix], prefix
            
    # ② 商品名 ＋ 商品説明文 の両方からキーワード判定
    combined_text = name_str + " " + desc_str
    
    if 'クロールバリエ' in combined_text or 'CV' in name_str.upper() or 'COULEUR VARIE' in combined_text.upper(): 
        return 'CO5377', 'CV'
    if 'newmo' in combined_text.lower() or 'nm' in name_str.lower(): 
        return 'NE4311', 'NM'
    if 'バスクラフト' in combined_text or 'BC' in name_str.upper() or 'BATH CRAFT' in combined_text.upper(): 
        return 'BA5378', 'BC'
    if 'BR' in name_str.upper(): 
        return 'BA5379', 'BR'
    if 'エル' in combined_text or 'EL' in name_str.upper() or 'ELEVAGE' in combined_text.upper(): 
        return 'EL8296', 'EL'
    
    # どちらからも判別できなかった場合
    return '', ''
    
def get_locondo_color(color_name):
    if not isinstance(color_name, str): return ''
    name = color_name.lower()
    if any(x in name for x in ['ブラック', '黒', 'black']): return 'ブラック'
    if any(x in name for x in ['ホワイト', '白', 'アイボリー', 'white']): return 'ホワイト'
    if any(x in name for x in ['グレー', '灰', 'チャコール', 'gray']): return 'グレー'
    if any(x in name for x in ['ブラウン', '茶', 'キャメル', 'モカ', 'チョコ']): return 'ブラウン'
    if any(x in name for x in ['ベージュ', 'オーク']): return 'ベージュ'
    if any(x in name for x in ['グリーン', '緑', 'カーキ', 'オリーブ']): return 'グリーン'
    if any(x in name for x in ['ネイビー', '紺', 'navy']): return 'ネイビー'
    if any(x in name for x in ['ブルー', '青', 'デニム', '水色']): return 'ブルー'
    if any(x in name for x in ['レッド', '赤', 'ワイン', 'ボルドー']): return 'レッド'
    if any(x in name for x in ['イエロー', '黄', 'マスタード']): return 'イエロー'
    if any(x in name for x in ['ピンク', '桃', 'pink']): return 'ピンク'
    if any(x in name for x in ['パープル', '紫', 'purple']): return 'パープル'
    if any(x in name for x in ['ゴールド', '金', 'gold']): return 'ゴールド'
    if any(x in name for x in ['シルバー', '銀', 'プラチナ']): return 'シルバー'
    if any(x in name for x in ['柄', 'マルチ', 'パイソン', 'レオパード', 'チェック', 'ドット', 'スネーク']): return 'マルチカラー'
    return ''

def clean_product_name(name):
    if not isinstance(name, str): return ''
    name = re.sub(r'【[^】]*】', '', name) # 【】内の装飾を除去
    for fw in FORBIDDEN_WORDS:
        name = re.sub(fw, '', name, flags=re.IGNORECASE)
    # 全角英数・スペースを半角に
    name = name.translate(str.maketrans(
        '０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ ',
        '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz '
    ))
    return re.sub(r'[ \t]+', ' ', name).strip()

def clean_html(text):
    if not isinstance(text, str): return ''
    
    # 1. HTMLコメントアウトを完全に削除
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    
    # 2. ロコンド禁止ワードの自動削除
    forbidden_words = [
        r'[0-9０-９]+(?:%|％)(?:OFF|off|オフ)', 
        r'[0-9０-９]+円引', 
        r'セール', r'激安', r'再値下げ', r'送料無料', r'返品不可', r'ネコポス'
    ]
    for fw in forbidden_words:
        text = re.sub(fw, '', text, flags=re.IGNORECASE)
    
    # 3. 改行や段落を意味するタグを、一時的に「\n（改行）」に変換
    text = re.sub(r'</?(p|div|li|center|tr|table|tbody|th|td)[^>]*>', '\n', text, flags=re.IGNORECASE)
    
    # 4. その他のすべてのHTMLタグを消去（★ br と font タグだけは残す）
    text = re.sub(r'<(?!/?(?:br|font)\b)[^>]+>', '', text, flags=re.IGNORECASE)
    
    # 5. Windows特有の見えない改行コード(\r)などを \n に統一
    text = text.replace('\r', '\n')
    
    # 6. スペース・タブの連続を1つのスペースに
    text = re.sub(r'[ \t]+', ' ', text)
    
    # 7. 全角の英数字とスペースを自動で半角に変換
    text = text.translate(str.maketrans(
        '０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ ',
        '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz '
    ))
    
    # 8. 改行コード(\n)を <br> に変換
    text = re.sub(r'(?:\s*\n\s*)+', '<br>', text)
    
    # 9. 【追加】元々入っていた <br> も含め、連続する <br> の連打を完全に1つに圧縮する
    text = re.sub(r'(?:\s*<br\s*/?>\s*)+', '<br>', text, flags=re.IGNORECASE)
    
    # 10. 先頭と末尾のゴミ（<br>やスペース）を綺麗に削除
    text = re.sub(r'^(?:<br>|\s)+|(?:<br>|\s)+$', '', text, flags=re.IGNORECASE)
    
    return text.strip()

def extract_origin(text):
    if not isinstance(text, str): return '中国'
    if re.search(r'日本|Japan|日本製', text, re.IGNORECASE): return '日本'
    return '中国'

def extract_width(text, brand_prefix):
    if isinstance(text, str):
        if re.search(r'[４4][ＥE]', text): return '4E（ゆったり）'
        if re.search(r'[３3][ＥE]', text): return '3E（ゆったり）'
        if re.search(r'[２2][ＥE]', text): return '2E（普通）'
        if re.search(r'[^０-９0-9][ＥE](?![a-zA-Z])', text): return 'E（普通）'
    return '3E（ゆったり）' if brand_prefix in ['CV', 'NM'] else '2E（普通）'

def extract_material(text, brand_prefix):
    detail = ''
    if isinstance(text, str):
        m = re.search(r'(?:<th[^>]*>素材</th>\s*<td[^>]*>|【素材】)(.*?)(?:</td>|<br>|】|\n|$)', text, re.IGNORECASE)
        if m:
            detail = re.sub(r'<[^>]+>', '', m.group(1)).strip()
    
    if re.search(r'本革|牛革|豚革|レザー', detail): hyo = '本革'
    elif re.search(r'合皮|合成皮革', detail): hyo = '合成皮革'
    elif re.search(r'ウルトラスエード|クラリーノ|トリベラ|人工皮革', detail): hyo = 'その他合成繊維'
    else:
        hyo = 'その他生地' if brand_prefix == 'CV' else 'その他合成繊維'
    return hyo, detail

def to_excl(price):
    return int(round(price / 1.1)) if pd.notna(price) else ''


# --- メイン生成処理 ---

def _read_csv_multi(files, encoding='cp932'):
    """単一ファイルまたはファイルのリストを受け取り、結合したDataFrameを返す"""
    if not isinstance(files, (list, tuple)):
        files = [files]
    dfs = []
    for f in files:
        df = pd.read_csv(f, encoding=encoding)
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def generate_locondo_csv(fs_var_file, ne_file, fs_goods_file, season_override: Optional[str] = None) -> GeneratorResult:
    fs_var = _read_csv_multi(fs_var_file)
    ne = pd.read_csv(ne_file, encoding='cp932')
    fs_goods = _read_csv_multi(fs_goods_file)

    fs_var = fs_var.drop_duplicates(subset=['商品管理番号'], keep='first')

    ne['jan_str'] = ne['ＪＡＮコード'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    fs_var['jan_str'] = fs_var['JANコード'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    
    merged = fs_var.merge(
        ne[['商品コード', '売価', '表示価格', '原価']], left_on='商品管理番号', right_on='商品コード', how='left'
    )
    fs_goods = fs_goods.drop_duplicates(subset=['商品URLコード'], keep='first')
    merged = merged.merge(
        fs_goods[['商品URLコード', '商品名', '商品説明文', '定価']], on='商品URLコード', how='left', suffixes=('', '_goods')
    )

    skip_mask = merged['売価'].isna()
    skip_count = skip_mask.sum()
    merged = merged[~skip_mask].copy()

    brand_info = merged.apply(lambda r: get_brand_info(r['商品URLコード'], r['商品名_goods'], r['商品説明文']), axis=1, result_type='expand')
    merged['_brand_code'] = brand_info[0]
    merged['_prefix'] = brand_info[1]
    
    brand_missing = merged[merged['_brand_code'] == '']['商品URLコード'].unique().tolist()
    merged['_color_code_upper'] = merged['バリエーション別枝番（縦軸）'].astype(str).str.upper()

    merged['_retail'] = merged.apply(
        lambda r: r['定価'] if pd.notna(r['定価']) else r['売価'], axis=1
    )

    merged['_origin'] = merged['商品説明文'].apply(extract_origin)
    merged['_width'] = merged.apply(lambda r: extract_width(r['商品説明文'], r['_prefix']), axis=1)
    
    mat_df = merged.apply(lambda r: extract_material(r['商品説明文'], r['_prefix']), axis=1, result_type='expand')
    merged['_hyo_sozai'] = mat_df[0]
    merged['_sozai_detail'] = mat_df[1]

    out = pd.DataFrame(index=merged.index)
    out['カテゴリー1']        = ''
    out['カテゴリーコード1']   = ''
    out['製品種別']           = 'シューズ'
    out['ジェンダー']          = 'レディース'
    out['ブランドコード']       = merged['_brand_code']
    out['シーズン']            = season_override if season_override else ''
    out['品番']               = merged['商品URLコード']
    out['カラーコード']         = merged['_color_code_upper']
    out['カラー']              = merged['_color_code_upper']
    out['ロコンドカラー']       = merged['バリエーション別選択肢（縦軸）'].apply(get_locondo_color)
    out['ロコンドサイズ']       = merged['バリエーション別選択肢（横軸）']
    out['サイズ']              = merged['バリエーション別選択肢（横軸）'].apply(lambda x: re.sub(r'[^\d.]', '', str(x)))
    out['サイズレンジ表記']     = ''
    out['サイズコード']         = merged['バリエーション別枝番（横軸）']
    out['JANコード']           = merged['jan_str']
    out['ハウスコード']         = merged['商品管理番号']
    
    # ここで強化された製品名クリーニングを適用
    out['製品名']              = merged['商品名_goods'].apply(clean_product_name)
    
    out['定価（税抜き）']       = merged['_retail'].apply(to_excl)
    out['定価（税込）']         = merged['_retail'].apply(lambda p: int(p) if pd.notna(p) else '')
    out['セール価格（税抜き）']  = ''
    out['セール価格（税込）']   = ''
    
    # ここで強化された商品説明文クリーニングを適用
    out['商品説明文']          = merged['商品説明文'].apply(clean_html)
    
    out['マーケットプレイス']   = 'ロコンド (LD)'
    out['カテゴリー2']         = ''
    out['カテゴリーコード2']    = ''
    out['原産国']              = merged['_origin']
    out['表素材']              = merged['_hyo_sozai']
    out['内側素材']            = '生地・合成繊維'
    out['中敷素材']            = '生地・合成繊維'
    out['ソール素材']          = '合成素材'
    out['靴幅']               = merged['_width']
    out['素材詳細']            = merged['_sozai_detail']
    out['洗濯表示']            = ''
    out['寸法詳細']            = ''
    out['備考']               = ''
    out['下代（税抜き）']       = merged['原価'].apply(lambda g: to_excl(g) if pd.notna(g) and g != 0 else '')
    out['価格表記タイプ']       = ''
    out['エラー内容']          = ''

    return GeneratorResult(
        df=out[MDS_COLUMNS].reset_index(drop=True),
        success_count=len(out),
        skip_count=int(skip_count),
        brand_missing=brand_missing
    )