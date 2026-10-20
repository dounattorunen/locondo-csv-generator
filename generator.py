import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional

import pandas as pd

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

# ブランドコード（ロコンドブランドマスタ brand_*.csv と一致・変更なし）
BRAND_MAP = {
    'CV': 'CO5377', 'NM': 'NE4311', 'BC': 'BA5378', 'BR': 'BA5379', 'EL': 'EL8296'
}

# ブランド名（ブランドマスタの 名称 / カタカナ）。ブランド判定と、製品名からのブランド名除去に使用
BRAND_NAMES = {
    'CV': ['COULEUR VARIE', 'クロールバリエ'],
    'NM': ['newmo', 'ニューモ'],
    'BC': ['BATH CRAFT', 'バスクラフト'],
    'BR': ['BATH ROOM', 'バスルーム'],
    'EL': ['elevage', 'エルバージュ'],
}

# どうしても自動判別できない品番は、ここに '品番': 'ブランド略称' の形式で追加してください。
BRAND_EXCEPTIONS = {
    '654130': 'BR',  # ロコンド出品データ上 BATH ROOM
    '654131': 'BR',  # ロコンド出品データ上 BATH ROOM
}

# NE商品マスター「商品タグ」→ ブランド略称（出品中データ125品番で全件一致を確認済み）
NE_TAG_BRAND = {
    'CV': 'CV', 'BG': 'CV', 'CVR': 'CV',   # クロールバリエ（BG=バッグ, CVR=ルームシューズ等）
    'CO': 'BC', 'BC': 'BC',                # BATH CRAFT
    'BR': 'BR',                            # BATH ROOM
    'NM': 'NM',                            # newmo（タグは [nm]）
    'EV': 'EL',                            # elevage（タグは [ev]）
}

# 商品名中のブランド略号（例: CV574233 / ev174551 / BR 960101 / 174605 NM甲深…）
BRAND_ABBR = {'CV': 'CV', 'NM': 'NM', 'BR': 'BR', 'BC': 'BC', 'EV': 'EL', 'EL': 'EL'}
_ABBR_PATTERN = re.compile(r'(?<![A-Za-z])(CV|NM|BR|BC|EV|EL)(?![A-Za-z])', re.IGNORECASE)

# 画面から登録した例外品番の保存先（app.py と同じフォルダ）
EXCEPTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'brand_exceptions.json')

# 画面表示用のブランド名
BRAND_LABELS = {
    'CV': 'COULEUR VARIE（クロールバリエ）', 'NM': 'newmo（ニューモ）', 'BC': 'BATH CRAFT（バスクラフト）',
    'BR': 'BATH ROOM（バスルーム）', 'EL': 'elevage（エルバージュ）',
}


def load_saved_exceptions():
    try:
        with open(EXCEPTIONS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return {str(k): v for k, v in data.items() if v in BRAND_MAP}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_exceptions(mapping):
    clean = {str(k).strip(): v for k, v in mapping.items() if str(k).strip() and v in BRAND_MAP}
    with open(EXCEPTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    return clean


# 全項目共通の禁止ワードリスト（景表法・ロコンドルール対応）
FORBIDDEN_WORDS = [
    r'[0-9０-９]+(?:%|％)(?:OFF|off|オフ)',
    r'[0-9０-９]+円引',
    r'セール', r'激安', r'再値下げ', r'送料無料', r'返品不可', r'ネコポス',
    r'大好評', r'最高', r'ベストセラー', r'完全防水', r'治す', r'治療'
]

# ロコンドで使用不可の「～」と絵文字・記号類
_TILDE = re.compile('[～〜]')
_EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]')

# 原産国（テンプレート「その他選択肢一覧」の選択肢）
COUNTRIES = [
    '日本', '中国', '韓国', '台湾', 'アメリカ', 'アルゼンチン', 'イタリア', 'イングランド', 'インド',
    'インドネシア', 'エルサルバドル', 'オランダ', 'カナダ', 'カンボジア', 'シンガポール', 'スペイン',
    'スリランカ', 'スロバキア', 'タイ', 'チリ', 'ドイツ', 'ドミニカ共和国', 'トルコ', 'ニカラグア',
    'バングラデシュ', 'フィリピン', 'ブラジル', 'フランス', 'ベトナム', 'ボスニア・ヘルツェゴビナ',
    'ポルトガル', 'マレーシア', 'ミャンマー', 'メキシコ', 'モロッコ', 'ルーマニア',
]
ORIGIN_ALIASES = {
    '中華人民共和国': '中国', '大韓民国': '韓国', 'イギリス': 'イングランド', '英国': 'イングランド',
    '米国': 'アメリカ', 'JAPAN': '日本', 'CHINA': '中国', 'KOREA': '韓国', 'TAIWAN': '台湾',
    'VIETNAM': 'ベトナム', 'ITALY': 'イタリア', 'SPAIN': 'スペイン', 'INDIA': 'インド',
    'INDONESIA': 'インドネシア', 'CAMBODIA': 'カンボジア', 'MYANMAR': 'ミャンマー',
    'THAILAND': 'タイ', 'PORTUGAL': 'ポルトガル', 'BRAZIL': 'ブラジル', 'FRANCE': 'フランス',
    'GERMANY': 'ドイツ', 'TURKEY': 'トルコ', 'BANGLADESH': 'バングラデシュ',
    'PHILIPPINES': 'フィリピン', 'MALAYSIA': 'マレーシア', 'MEXICO': 'メキシコ',
}

# カラーコード → ロコンドカラー（ロコンド出品中データ commodity_20261020 の実績から作成）
COLOR_CODE_MAP = {
    'ABU': 'ブルー', 'BE': 'ベージュ', 'BGR': 'ブルー', 'BL': 'ブラック', 'BLACK': 'ブラック', 'BR': 'ブラウン',
    'BRED': 'レッド', 'BU': 'ブルー', 'CA': 'ベージュ', 'CBE': 'ベージュ', 'CORAL': 'ピンク', 'DBL': 'ブラック',
    'DBR': 'ブラウン', 'DENIM': 'ブルー', 'DGBE': 'ベージュ', 'DGR': 'グリーン', 'DGY': 'グレー', 'DMA': 'イエロー',
    'DNAV': 'ネイビー', 'DOR': 'オレンジ', 'DRE': 'レッド', 'EBL': 'ブラック', 'EGR': 'グリーン', 'ENAV': 'ネイビー',
    'GBE': 'ベージュ', 'GBU': 'ブルー', 'GCH': 'グレー', 'GGR': 'グリーン', 'GO': 'ゴールド', 'GR': 'グリーン',
    'GY': 'グレー', 'INDIGO': 'ネイビー', 'IV': 'ホワイト', 'KA': 'カーキ', 'LAV': 'パープル', 'LBE': 'ベージュ',
    'LBR': 'ブラウン', 'LGR': 'グリーン', 'LGY': 'グレー', 'LKA': 'カーキ', 'LPU': 'パープル', 'LWH': 'ホワイト',
    'MA': 'イエロー', 'MBL': 'ブラック', 'MINT': 'グリーン', 'MOCA': 'ブラウン', 'NAV': 'ネイビー', 'OGR': 'グリーン',
    'OK': 'ベージュ', 'OLV': 'カーキ', 'OLV2': 'グリーン', 'OR': 'オレンジ', 'OWH': 'ホワイト', 'OWH2': 'ホワイト',
    'PBE': 'ピンク', 'PGR': 'グリーン', 'PI': 'ピンク', 'PL': 'パープル', 'POR': 'オレンジ', 'PRPI': 'ピンク',
    'PYTHON': 'アニマル', 'RE': 'レッド', 'ROSE': 'レッド', 'RPI': 'ピンク', 'RPIS': 'ピンク', 'SBL': 'ブラック',
    'SBR': 'ブラウン', 'SI': 'シルバー', 'SLBE': 'ベージュ', 'SNAKE': 'アニマル', 'STP': 'ベージュ', 'TP': 'ベージュ',
    'VRE': 'レッド', 'WH': 'ホワイト', 'WIN': 'レッド', 'YE': 'イエロー', 'ZEB': 'アニマル', 'PYS': 'アニマル',
    'LEO': 'アニマル',
}

# カテゴリ（テンプレート「カテゴリ一覧」より）コード → カテゴリー1表記
CATEGORY_PATHS = {
    'pumps_plain': 'レディースファッション > シューズ > パンプス > ベーシックパンプス',
    'pumps_strap': 'レディースファッション > シューズ > パンプス > ストラップ付きパンプス',
    'l_flatpumps': 'レディースファッション > シューズ > パンプス > フラットパンプス',
    'mule_pumps': 'レディースファッション > シューズ > パンプス > ミュールパンプス',
    'pumps_open': 'レディースファッション > シューズ > パンプス > オープントゥパンプス',
    'pumps_wedge': 'レディースファッション > シューズ > パンプス > ウェッジソールパンプス',
    'pumps_platform': 'レディースファッション > シューズ > パンプス > プラットフォームパンプス',
    'pumps_party': 'レディースファッション > シューズ > パンプス > パーティーパンプス',
    'sandal_l_flat': 'レディースファッション > シューズ > サンダル > フラットサンダル',
    'sandal_l_wedge': 'レディースファッション > シューズ > サンダル > ウェッジソールサンダル',
    'sandal_l_plat': 'レディースファッション > シューズ > サンダル > プラットフォームサンダル',
    'aa11650': 'レディースファッション > シューズ > サンダル > ミュール',
    'strapsandals': 'レディースファッション > シューズ > サンダル > ストラップ付きサンダル',
    'gurkhasandals_l': 'レディースファッション > シューズ > サンダル > グルカサンダル',
    'sandal_l_sport': 'レディースファッション > シューズ > サンダル > スポーツサンダル',
    'showersandal_l': 'レディースファッション > シューズ > サンダル > シャワーサンダル',
    'sandal_l_beach': 'レディースファッション > シューズ > サンダル > ビーチサンダル',
    'sandals_sabot': 'レディースファッション > シューズ > サンダル > サボサンダル',
    'babouche_l': 'レディースファッション > シューズ > サンダル > バブーシュ',
    'aa11680': 'レディースファッション > シューズ > サンダル > ブーツサンダル',
    'shortboots_l': 'レディースファッション > シューズ > ブーツ > ショートブーツ',
    'middleboots_l': 'レディースファッション > シューズ > ブーツ > ミドルブーツ',
    'longboots_l': 'レディースファッション > シューズ > ブーツ > ロングブーツ',
    'booties_l': 'レディースファッション > シューズ > ブーツ > ブーティ',
    'kneehighboots_l': 'レディースファッション > シューズ > ブーツ > ニーハイブーツ',
    'mouton_ladies': 'レディースファッション > シューズ > ブーツ > ムートンブーツ',
    'workboots_l': 'レディースファッション > シューズ > ブーツ > ワーク・エンジニアブーツ',
    'aa11180': 'レディースファッション > シューズ > スニーカー > ローカットスニーカー',
    'aa11190': 'レディースファッション > シューズ > スニーカー > ミドル・ハイカットスニーカー',
    'heel_sneaker': 'レディースファッション > シューズ > スニーカー > 厚底・ダッドスニーカー',
    'sneaker_slipon_l': 'レディースファッション > シューズ > スニーカー > スリッポンスニーカー',
    'ballet2': 'レディースファッション > シューズ > バレエシューズ',
    'laceup_l': 'レディースファッション > シューズ > レースアップ・ローファー・ドレスシューズ > レースアップ',
    'loafer_l': 'レディースファッション > シューズ > レースアップ・ローファー・ドレスシューズ > ローファー',
    'flat_opera': 'レディースファッション > シューズ > レースアップ・ローファー・ドレスシューズ > オペラシューズ',
    'moccassin_l': 'レディースファッション > シューズ > モカシン・カジュアルシューズ > モカシンシューズ',
    'casual_slipon_l': 'レディースファッション > シューズ > モカシン・カジュアルシューズ > スリッポンカジュアルシューズ',
    'rainboots': 'レディースファッション > シューズ > レイン・スノーシューズ > レインブーツ',
    'snowboots': 'レディースファッション > シューズ > レイン・スノーシューズ > スノーブーツ',
    'rainpumps': 'レディースファッション > シューズ > レイン・スノーシューズ > レインパンプス',
    'rainsneaker': 'レディースファッション > シューズ > レイン・スノーシューズ > レインスニーカー',
    'rainloafer': 'レディースファッション > シューズ > レイン・スノーシューズ > レインローファー',
    'roomshoes_ladies': 'ホームファッション > インテリア > ルームシューズ・スリッパ > レディースルームシューズ',
    'l_size_handbag': 'レディースファッション > バッグ > ハンドバッグ > ハンドバッグ L・A4',
    'm_size_handbag': 'レディースファッション > バッグ > ハンドバッグ > ハンドバッグ M',
    's_size_handbag': 'レディースファッション > バッグ > ハンドバッグ > ミニハンドバッグ',
    'b_handbag': 'レディースファッション > バッグ > ハンドバッグ > バケツバッグ',
    'k_handbag': 'レディースファッション > バッグ > ハンドバッグ > 巾着バッグ',
    'c_handbag': 'レディースファッション > バッグ > ハンドバッグ > サークルバッグ',
    'l_size_tote': 'レディースファッション > バッグ > トートバッグ > トートバッグ L・A4',
    'm_size_tote': 'レディースファッション > バッグ > トートバッグ > トートバッグ M',
    's_size_tote': 'レディースファッション > バッグ > トートバッグ > ミニトートバッグ',
    'l_size_shoulder': 'レディースファッション > バッグ > ショルダーバッグ > ショルダーバッグ L・A4',
    'm_size_shoulder': 'レディースファッション > バッグ > ショルダーバッグ > ショルダーバッグ M',
    's_size_shoulder': 'レディースファッション > バッグ > ショルダーバッグ > ミニショルダーバッグ',
    'l_size_backpack': 'レディースファッション > バッグ > リュック・バックパック > リュック L・A4',
    'm_size_backpack': 'レディースファッション > バッグ > リュック・バックパック > リュック M',
    's_size_backpack': 'レディースファッション > バッグ > リュック・バックパック > ミニリュック',
    'bodybag_l': 'レディースファッション > バッグ > ボディバッグ・サコッシュ',
    'clutchbag': 'レディースファッション > バッグ > クラッチ・パーティバッグ',
    'basketbag': 'レディースファッション > バッグ > カゴバッグ',
    'plasticbag': 'レディースファッション > バッグ > クリアバッグ',
    'furbag': 'レディースファッション > バッグ > ファーバッグ',
    'bostonbags': 'レディースファッション > バッグ > ボストンバッグ',
    'ecobag': 'レディースファッション > バッグ > エコバッグ',
    'mothers_bag': 'レディースファッション > バッグ > ママバッグ',
    'walletbag': 'レディースファッション > 財布・ケース・小物 > ウォレットバッグ',
}


def _both(a, b):
    """a と b を両方含む（順不同）"""
    return rf'(?:{a}).*(?:{b})|(?:{b}).*(?:{a})'


# 製品名から判定するカテゴリ（上から順に、最初に一致したもの）。一致なしは空欄＝ロコンド既定カテゴリ
SHOE_CATEGORY_RULES = [
    (r'ブーツサンダル', 'aa11680'),
    (r'レインブーツ|長靴|ラバーブーツ', 'rainboots'),
    (r'レインパンプス', 'rainpumps'),
    (r'レインスニーカー', 'rainsneaker'),
    (r'レインローファー', 'rainloafer'),
    (r'スノーブーツ', 'snowboots'),
    (_both('ムートン', 'ブーツ'), 'mouton_ladies'),
    (r'ニーハイ', 'kneehighboots_l'),
    (r'ロングブーツ', 'longboots_l'),
    (r'ミドルブーツ|ミドル丈ブーツ', 'middleboots_l'),
    (r'エンジニアブーツ|ワークブーツ', 'workboots_l'),
    (r'ブーティ', 'booties_l'),
    (r'ショートブーツ|アンクルブーツ|サイドゴアブーツ|チェルシーブーツ', 'shortboots_l'),
    (r'ルームシューズ|スリッパ', 'roomshoes_ladies'),
    (r'バブーシュ', 'babouche_l'),
    (r'ミュールパンプス', 'mule_pumps'),
    (r'スポーツサンダル', 'sandal_l_sport'),
    (r'ビーチサンダル', 'sandal_l_beach'),
    (r'シャワーサンダル', 'showersandal_l'),
    (r'グルカ', 'gurkhasandals_l'),
    (r'サボ(?!テン)', 'sandals_sabot'),
    (_both('ウェッジ', 'サンダル'), 'sandal_l_wedge'),
    (_both('厚底|プラットフォーム', 'サンダル'), 'sandal_l_plat'),
    (_both('ストラップ', 'サンダル'), 'strapsandals'),
    (_both('フラット', 'サンダル'), 'sandal_l_flat'),
    (r'ミュール', 'aa11650'),
    (_both('厚底|ダッド', 'スニーカー'), 'heel_sneaker'),
    (_both('スリッポン', 'スニーカー'), 'sneaker_slipon_l'),
    (_both('ハイカット|ミドルカット', 'スニーカー'), 'aa11190'),
    (r'スニーカー', 'aa11180'),
    (r'バレエ', 'ballet2'),
    (r'ローファー', 'loafer_l'),
    (r'オペラ', 'flat_opera'),
    (r'モカシン', 'moccassin_l'),
    (r'レースアップ|オックスフォード', 'laceup_l'),
    (_both('オープントゥ', 'パンプス'), 'pumps_open'),
    (_both('ウェッジ', 'パンプス'), 'pumps_wedge'),
    (_both('ストラップ', 'パンプス'), 'pumps_strap'),
    (_both('フラット', 'パンプス'), 'l_flatpumps'),
    (_both('厚底|プラットフォーム', 'パンプス'), 'pumps_platform'),
    (_both('パーティ', 'パンプス'), 'pumps_party'),
    (r'パンプス', 'pumps_plain'),
    (r'スリッポン', 'casual_slipon_l'),
]

_BAG_SMALL = r'ミニ|スモール|プチ'
_BAG_LARGE = r'A4|大容量|ビッグ|大きめ|ラージ'


def _sized(base):
    return lambda n: ('s_size_' if re.search(_BAG_SMALL, n) else
                      'l_size_' if re.search(_BAG_LARGE, n) else 'm_size_') + base


BAG_CATEGORY_RULES = [
    (r'ウォレットバッグ|お財布バッグ', 'walletbag'),
    (r'カゴ|かご|籠', 'basketbag'),
    (r'クリアバッグ|PVCバッグ', 'plasticbag'),
    (_both('ファー', 'バッグ'), 'furbag'),
    (r'巾着', 'k_handbag'),
    (r'バケツ', 'b_handbag'),
    (r'サークルバッグ|丸型バッグ', 'c_handbag'),
    (r'クラッチ|パーティーバッグ|パーティバッグ', 'clutchbag'),
    (r'ボディバッグ|サコッシュ|ウエストバッグ', 'bodybag_l'),
    (r'ボストン', 'bostonbags'),
    (r'エコバッグ', 'ecobag'),
    (r'マザーズ|ママバッグ', 'mothers_bag'),
    (r'リュック|バックパック|デイパック', _sized('backpack')),
    (r'ショルダー|ポシェット', _sized('shoulder')),
    (r'トート', _sized('tote')),
    (r'ハンドバッグ', _sized('handbag')),
]

BAG_WORDS = r'バッグ|バック(?![ァ-ヶー])|トート|リュック|バックパック|ショルダー|サコッシュ|クラッチ|ボストン|ポシェット|デイパック'
SHOE_WORDS = r'パンプス|サンダル|ブーツ|ブーティ|スニーカー|シューズ|ローファー|ミュール|スリッポン|モカシン|バレエ|レースアップ|オックスフォード|スリッパ|バブーシュ|靴'


@dataclass
class GeneratorResult:
    df: pd.DataFrame
    success_count: int
    skip_count: int
    brand_missing: list = field(default_factory=list)      # ブランド判別不可の品番
    brand_conflict: list = field(default_factory=list)     # 品番の頭文字と商品名のブランドが食い違う品番
    type_uncertain: list = field(default_factory=list)     # 製品種別を判定できず「シューズ」にした品番
    category_missing: list = field(default_factory=list)   # カテゴリ未判定（ロコンド既定カテゴリになる）品番
    size_invalid: list = field(default_factory=list)       # ロコンドサイズに変換できなかった「品番:サイズ」
    jan_missing: list = field(default_factory=list)        # JANコードが空のため除外したSKU（商品管理番号）


# --- 共通ユーティリティ ---

_ZEN = (''.join(chr(0xFF10 + i) for i in range(10)) + ''.join(chr(0xFF21 + i) for i in range(26))
        + ''.join(chr(0xFF41 + i) for i in range(26)) + '　')
_HAN = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz '
_ZEN2HAN = str.maketrans(_ZEN, _HAN)


def _to_hankaku(text):
    return text.translate(_ZEN2HAN)


def _s(value):
    """NaN/None を空文字にして文字列化（'nan' を出力しない）"""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return '' if s.lower() == 'nan' else s


def _code_str(value):
    """数値として読まれたコードの '.0' を除去"""
    return re.sub(r'\.0$', '', _s(value))


# 禁止ワードを含むが意味を残したい表現は、削除前に言い換える
FORBIDDEN_REPLACEMENTS = [
    (r'完全防水(?:では|で)?(?:ありません|ございません|ない)', '防水ではありません'),
]


def _remove_forbidden(text):
    for pattern, repl in FORBIDDEN_REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    for fw in FORBIDDEN_WORDS:
        text = re.sub(fw, '', text, flags=re.IGNORECASE)
    return text


def _remove_symbols(text):
    text = _TILDE.sub('-', text)
    return _EMOJI.sub('', text)


def _html_to_text(text):
    """判定用にHTMLをプレーンテキスト化（th→td は「：」で連結、ブロック要素は改行）"""
    if not isinstance(text, str):
        return ''
    t = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    t = re.sub(r'</th>\s*<td[^>]*>', '：', t, flags=re.IGNORECASE)
    t = re.sub(r'<br\s*/?>|</?(?:p|div|li|tr|table|tbody|center|dt|dd)\b[^>]*>|</t[hd]>', '\n', t, flags=re.IGNORECASE)
    t = re.sub(r'<[^>]+>', '', t)
    return _to_hankaku(t.replace('\r', '\n'))


# --- ブランド判定 ---

def _brand_pattern(name):
    body = r'\s*'.join(re.escape(p) for p in name.split(' '))
    if re.fullmatch(r'[A-Za-z ]+', name):
        # 英字ブランドは前後が英字でないこと（"BATHROOM" のようなスペース無しも許容）
        return re.compile(r'(?<![A-Za-z])' + body + r'(?![A-Za-z])', re.IGNORECASE)
    return re.compile(body)


_BRAND_PATTERNS = {p: [_brand_pattern(n) for n in names] for p, names in BRAND_NAMES.items()}


def _brands_in(text):
    t = _to_hankaku(_s(text))
    return {p for p, pats in _BRAND_PATTERNS.items() if any(pt.search(t) for pt in pats)}


_CODE_TO_PREFIX = {v: k for k, v in BRAND_MAP.items()}


def _abbrs_in(text):
    t = _to_hankaku(_s(text))
    return {BRAND_ABBR[m.upper()] for m in _ABBR_PATTERN.findall(t)}


def _guess_brand(code, item_name, description):
    """ロコンド出品中データに無い品番の推定。(略称, 食い違いフラグ)"""
    name_hits = _brands_in(item_name) | _abbrs_in(item_name)

    m = re.match(r'^([A-Za-z]+)', code)
    if m and m.group(1).upper() in BRAND_MAP:
        p = m.group(1).upper()
        return p, bool(name_hits) and p not in name_hits

    if len(name_hits) == 1:
        return next(iter(name_hits)), False
    if not name_hits:
        desc_hits = _brands_in(_html_to_text(description))
        if len(desc_hits) == 1:
            return next(iter(desc_hits)), False
    return '', False


def ne_tag_brands(tag):
    """NE商品タグ（例: '[nm][TikTokShop出品]'）からブランド略称の集合を返す"""
    found = set()
    for t in re.findall(r'\[([^\]]+)\]', _s(tag)):
        p = NE_TAG_BRAND.get(_to_hankaku(t).strip().upper())
        if p:
            found.add(p)
    return found


def get_brand_info(item_code, item_name, description, reference=None, ne_tag_set=None, ne_name='', fs_group='',
                   exceptions=None):
    """(ブランドコード, ブランド略称, 食い違いフラグ) を返す

    判定順: ① ロコンド出品中データ（品番→ブランドコード）→ ② 例外品番
            → ③ NE商品タグ（[CV][BG][CVR]/[CO][BC]/[BR]/[nm]/[ev]）
            → ④ 品番の頭文字 → ⑤ FS商品名・NE商品名・FSメイングループのブランド名・略号
            → ⑥ 商品説明文のブランド名（1ブランドだけ出てくる場合のみ）
    """
    code = locondo_hinban(item_code)
    names = f'{_s(item_name)} {_s(ne_name)} {_s(fs_group)}'
    guess, guess_conflict = _guess_brand(code, names, description)
    tag_set = ne_tag_set or set()

    if reference and code in reference:
        bcode = reference[code]
        p = _CODE_TO_PREFIX.get(bcode, '')
        others = (tag_set | ({guess} if guess else set())) - {p}
        return bcode, p, bool(others)
    exc_map = {**BRAND_EXCEPTIONS, **{locondo_hinban(k): v for k, v in (exceptions or {}).items()}}
    exc = exc_map.get(code) or exc_map.get(code.lower())
    if exc:
        return BRAND_MAP[exc], exc, bool(tag_set - {exc})
    if len(tag_set) == 1:
        p = next(iter(tag_set))
        return BRAND_MAP[p], p, bool(guess) and guess != p
    if guess:
        return BRAND_MAP[guess], guess, guess_conflict or len(tag_set) > 1
    return '', '', len(tag_set) > 1


def load_locondo_reference(files):
    """ロコンド出品中データ（commodity_*.csv）から 品番→ブランドコード を作る
    ブランドコードは「ロコンド色SKU」の先頭6文字（例: CO5377BW000700 → CO5377）"""
    if not files:
        return {}
    df = _load_checked(files, 'ref', '4. ロコンド出品中データ')
    df = df.dropna(subset=['品番', 'ロコンド色SKU'])
    ref = {}
    for hinban, sku in zip(df['品番'], df['ロコンド色SKU']):
        bcode = str(sku).strip()[:6]
        if bcode in _CODE_TO_PREFIX:
            ref.setdefault(locondo_hinban(hinban), bcode)
    return ref


# --- 製品種別・カテゴリ ---

def detect_type_and_category(item_name):
    """(製品種別, カテゴリーコード, 種別が不確実か) を返す"""
    n = _to_hankaku(_s(item_name))
    is_bag = bool(re.search(BAG_WORDS, n))
    is_shoe = bool(re.search(SHOE_WORDS, n))
    if re.search(r'バッグ|バック(?![ァ-ヶー])', n):  # 「バレエ刺繍バッグ」「CV バック」等。「バックベルト」は除外
        is_bag, is_shoe = True, False
    if is_bag and not is_shoe:
        ptype, rules = 'バッグ', BAG_CATEGORY_RULES
    elif is_shoe and not is_bag:
        ptype, rules = 'シューズ', SHOE_CATEGORY_RULES
    else:
        return 'シューズ', '', True
    for pattern, code in rules:
        if re.search(pattern, n):
            return ptype, (code(n) if callable(code) else code), False
    return ptype, '', False


# --- 各種抽出・変換ロジック ---

def get_locondo_color(color_name, color_code=''):
    """① カラーコード表（ロコンド出品実績）→ ② カラー名 → ③ コードの先頭部分（BL/BR→BL）"""
    code = _to_hankaku(_s(color_code)).upper()
    if code in COLOR_CODE_MAP:  # ロコンド出品実績のあるコードはその値に揃える
        return COLOR_CODE_MAP[code]
    by_name = _color_from_name(color_name)
    if by_name not in ('', '他'):
        return by_name
    head = re.split(r'[/]', code)[0]
    if head in COLOR_CODE_MAP:
        return COLOR_CODE_MAP[head]
    return by_name or '他'


def _color_from_name(color_name):
    if not isinstance(color_name, str) or not color_name: return ''
    name = _to_hankaku(color_name).lower()
    # 柄・複数色は単色より優先
    if any(x in name for x in ['レオパード', 'ヒョウ', '豹', 'ゼブラ', 'パイソン', 'ダルメシアン', 'アニマル']): return 'アニマル'
    if any(x in name for x in ['マルチ', 'コンビ', 'mix', 'ミックス']): return 'マルチカラー'
    if any(x in name for x in ['柄', 'チェック', 'ドット', 'ストライプ', 'ボーダー', 'ペイズリー']): return 'パターン'
    if any(x in name for x in ['ブラック', '黒', 'black']): return 'ブラック'
    if any(x in name for x in ['ホワイト', '白', 'アイボリー', 'white']): return 'ホワイト'
    if any(x in name for x in ['グレージュ', 'ベージュ', 'オーク', 'サンド', 'beige']): return 'ベージュ'
    if any(x in name for x in ['グレー', '灰', 'チャコール', 'gray', 'grey']): return 'グレー'
    if any(x in name for x in ['ブラウン', '茶', 'キャメル', 'モカ', 'チョコ', 'トープ', 'brown']): return 'ブラウン'
    if any(x in name for x in ['カーキ', 'khaki']): return 'カーキ'
    if any(x in name for x in ['グリーン', '緑', 'オリーブ', 'ミント', 'green']): return 'グリーン'
    if any(x in name for x in ['ネイビー', '紺', 'navy']): return 'ネイビー'
    if any(x in name for x in ['ブルー', '青', 'デニム', '水色', 'サックス', 'blue']): return 'ブルー'
    if any(x in name for x in ['オレンジ', 'テラコッタ', 'orange']): return 'オレンジ'
    if any(x in name for x in ['レッド', '赤', 'ワイン', 'ボルドー', 'red']): return 'レッド'
    if any(x in name for x in ['ゴールド', '金', 'gold']): return 'ゴールド'
    if any(x in name for x in ['シルバー', '銀', 'プラチナ', 'silver']): return 'シルバー'
    if any(x in name for x in ['イエロー', '黄', 'マスタード', 'yellow']): return 'イエロー'
    if any(x in name for x in ['ピンク', '桃', 'pink']): return 'ピンク'
    if any(x in name for x in ['パープル', '紫', 'ラベンダー', 'purple']): return 'パープル'
    if any(x in name for x in ['スネーク', 'クロコ']): return 'パターン'
    return '他'


_PROMO = re.compile(r'SALE|OFF|オフ|ポイント|クーポン|送料|再入荷|入荷|予約|新作|新色|限定|値下|円|%|％|タイムセール|人気|定番|話題|ランキング|QVC|放映|おすすめ|オススメ', re.IGNORECASE)


def _is_promo(bracket):
    if _PROMO.search(bracket):
        return True
    return any(re.search(fw, bracket, re.IGNORECASE) for fw in FORBIDDEN_WORDS)


def clean_product_name(name):
    """ロコンド規定: ブランド名を含まない製品名。禁止ワード・【】装飾・「～」・絵文字も除去"""
    if not isinstance(name, str): return ''
    name = _to_hankaku(name)
    name = re.sub(r'[※].*$', '', name)                                   # 「※返品対応可」等の注記以降を除去
    name = re.sub(r'[(（][^)）]*[,、][^)）]*[)）]', '', name)              # (バレエコア,レオパード,…) のキーワード羅列を除去
    name = re.sub(r'^\s*No\.\s*', '', name, flags=re.IGNORECASE)       # 先頭の「No.」
    name = re.sub(r'\s*/\s*\d{4,}[A-Za-z]?(?=\s|[^\x00-\x7F]|$)', ' ', name)  # 「/944201」等の別品番
    name = re.sub(r'【[^】]*】', lambda m: '' if _is_promo(m.group(0)) else m.group(0), name)
    name = _remove_forbidden(name)
    for pats in _BRAND_PATTERNS.values():
        for pt in pats:
            name = pt.sub('', name)
    name = _EMOJI.sub('', _TILDE.sub(' ', name))  # 製品名の「～」は区切りとして空白に
    name = re.sub(r'[ \t]+', ' ', name).strip()
    # ブランド名除去で先頭・末尾に残った区切り記号を掃除
    return re.sub(r'^[\s/|｜・:：\-]+|[\s/|｜・:：\-]+$', '', name)


# FS商品説明文の色見本ブロック: <p><strong>カラーバリエーション</strong></p> … </table>
_COLOR_SWATCH = re.compile(
    r'<p[^>]*>\s*(?:<strong>)?\s*カラーバリエーション\s*(?:</strong>)?\s*</p>.*?</table>',
    re.DOTALL | re.IGNORECASE,
)


def clean_html(text):
    if not isinstance(text, str): return ''

    # 1. HTMLコメントアウトを完全に削除
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # 1-2. 色見本（「カラーバリエーション」見出し〜色見本の表）を削除
    text = _COLOR_SWATCH.sub('', text)
    text = re.sub(r'<p[^>]*>\s*(?:<strong>)?\s*カラーバリエーション\s*(?:</strong>)?\s*</p>', '', text)

    # 2. ロコンド禁止ワード（全項目共通リスト）と「～」・絵文字の除去
    text = _remove_forbidden(text)
    text = _remove_symbols(text)

    # 3. 改行や段落を意味するタグを、一時的に「\n（改行）」に変換
    text = re.sub(r'</?(p|div|li|center|tr|table|tbody|th|td)\b[^>]*>', '\n', text, flags=re.IGNORECASE)

    # 4. その他のすべてのHTMLタグを消去（★ br と font タグだけは残す）
    text = re.sub(r'<(?!/?(?:br|font)\b)[^>]+>', '', text, flags=re.IGNORECASE)

    # 5. Windows特有の見えない改行コード(\r)などを \n に統一
    text = text.replace('\r', '\n')

    # 6. 全角の英数字とスペースを半角に変換
    text = _to_hankaku(text)

    # 7. スペース・タブの連続を1つのスペースに
    text = re.sub(r'[ \t]+', ' ', text)

    # 8. 改行コード(\n)を <br> に変換
    text = re.sub(r'(?:\s*\n\s*)+', '<br>', text)

    # 9. 連続する <br> を1つに圧縮
    text = re.sub(r'(?:\s*<br\s*/?>\s*)+', '<br>', text, flags=re.IGNORECASE)

    # 10. 先頭と末尾の <br> やスペースを削除
    text = re.sub(r'^(?:<br>|\s)+|(?:<br>|\s)+$', '', text, flags=re.IGNORECASE)

    return text.strip()


def _match_country(value):
    v = _to_hankaku(value).upper()
    for alias, country in ORIGIN_ALIASES.items():
        if alias.upper() in v:
            return country
    for country in sorted(COUNTRIES, key=len, reverse=True):
        if country in value:
            return country
    return ''


def extract_origin(text):
    """原産国欄 → Made in 表記 → 「日本製」の順で判定。見つからなければ中国
    （以前は「日本人の足に」など本文中の「日本」にも反応していた）"""
    t = _html_to_text(text)
    m = re.search(r'(?:原産国|生産国|製造国|原産地|生産地)\s*[】\]]?\s*[:：]?\s*([^\n]+)', t)
    if m:
        c = _match_country(m.group(1))
        if c: return c
    m = re.search(r'made\s+in\s+([A-Za-z]+)', t, re.IGNORECASE)
    if m:
        c = _match_country(m.group(1))
        if c: return c
    if '日本製' in t: return '日本'
    return '中国'


_WIDTH_LABELS = {'4E': '4E（ゆったり）', 'EEEE': '4E（ゆったり）', '3E': '3E（ゆったり）', 'EEE': '3E（ゆったり）',
                 '2E': '2E（普通）', 'EE': '2E（普通）', 'E': 'E（普通）'}


def extract_width(text, brand_prefix):
    """ワイズ表記から靴幅を判定。単独の「E」はワイズ/足幅の文脈がある場合のみ採用
    （以前は "COULEUR VARIE" など英単語末尾の E にも反応していた）"""
    t = _html_to_text(text)
    m = re.search(r'(?:ワイズ|ウィズ|ウイズ|足幅|靴幅|足囲)\s*[】\]]?\s*[:：]?\s*(?:約\s*)?(EEEE|EEE|EE|[2-4]E|E)(?![A-Za-z])', t)
    if m:
        return _WIDTH_LABELS[m.group(1)]
    m = re.search(r'(?<![A-Za-z0-9.])(EEEE|EEE|[2-4]E)(?![A-Za-z])', t)
    if m:
        return _WIDTH_LABELS[m.group(1)]
    # 既定値はロコンド出品データの傾向に合わせる（BATH CRAFT のみ 2E が多数）
    return '2E（普通）' if brand_prefix == 'BC' else '3E（ゆったり）'


def _classify_material(seg, brand_prefix):
    # 合皮の判定を本革より先に（「PUレザー」「フェイクレザー」を本革にしない）
    if re.search(r'合成皮革|合皮|フェイクレザー|PUレザー|ビニールレザー|エコレザー|ヴィーガンレザー|(?<![A-Za-z])PU(?![A-Za-z])', seg):
        return '合成皮革'
    if re.search(r'ウルトラスエード|クラリーノ|トリベラ|人工|フェイクスエード|マイクロファイバー', seg):  # 人工皮革・人工スエード
        return 'その他合成繊維'
    if re.search(r'エナメル|パテント', seg): return 'エナメル'
    if re.search(r'スエード|スウェード|ヌバック', seg): return 'スエード'
    if re.search(r'本革|牛革|豚革|羊革|山羊革|ゴートスキン|カーフ|キップ|レザー', seg): return '本革'
    if re.search(r'デニム', seg): return 'デニム'
    if re.search(r'リネン|麻', seg): return 'リネン（麻）'
    if re.search(r'コットン|綿|キャンバス', seg): return 'コットン'
    if re.search(r'ウール|ファー|ボア', seg): return 'ウール・ニット・ファー'  # 靴の「ニット」は合成繊維が多いため対象外
    if re.search(r'ナイロン', seg): return 'ナイロン'
    if re.search(r'ポリエステル|ポリウレタン|アクリル|レーヨン|合成繊維', seg): return 'その他合成繊維'
    if re.search(r'PVC|ビニール', seg): return 'ビニール'
    if re.search(r'ラバー|ゴム|EVA', seg): return 'ラバー・EVA'
    return 'その他合成繊維'  # ロコンド出品データの大半がこの値


def extract_material(text, brand_prefix):
    """素材欄（<th>素材</th><td>… / 【素材】… / 素材：…）を素材詳細として抜き出し、
    アッパー（甲・表地・本体）部分から表素材を判定"""
    detail = ''
    lines = [l.strip() for l in _html_to_text(text).split('\n')]
    pat = re.compile(r'^(?:【\s*(?:表?素材|材質)\s*】\s*[:：]?|[\[［]?\s*(?:表?素材|材質)\s*[\]］]?\s*[:：])\s*(.*)$')
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            detail = m.group(1).strip()
            if not detail:  # 見出しだけの行 → 次の空でない行を値とする
                detail = next((l for l in lines[i + 1:] if l), '')
            break
    detail = re.sub(r'\s+', ' ', _remove_symbols(detail)).strip()

    segs = [s for s in re.split(r'[／/、,|｜]', detail) if s.strip()]
    upper = next((s for s in segs if re.search(r'アッパー|甲|表地|表材|本体', s)), segs[0] if segs else '')
    return _classify_material(upper, brand_prefix), detail


def _to_decimal(value):
    s = _s(value).replace(',', '')
    if not s: return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def to_excl(price):
    """税込→税抜（四捨五入。Python の round() は偶数丸めのため Decimal で計算）"""
    d = _to_decimal(price)
    if d is None: return ''
    return int((d / Decimal('1.1')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def to_int(price):
    d = _to_decimal(price)
    return '' if d is None else int(d.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _fmt_cm(d):
    return f'{int(d)}cm' if d == d.to_integral() else f'{d.normalize():f}cm'


def convert_size(raw, ptype):
    """FSの横軸選択肢 → (ロコンドサイズ, サイズ, サイズレンジ表記, 変換OKか)
    シューズのロコンドサイズは '23cm' '23.5cm' 形式（10〜32cm, 0.5刻み）、バッグは FREE/S/M/L"""
    s = _to_hankaku(_s(raw))
    if ptype == 'バッグ':
        u = s.upper().replace(' ', '')
        if u in ('S', 'M', 'L'): return u, '', '', True
        if u in ('', 'F', 'FREE', 'フリー', 'フリーサイズ', 'ワンサイズ', 'ONESIZE'): return 'FREE', '', '', True
        return 'FREE', s, '', True

    nums = [Decimal(n) for n in re.findall(r'\d+(?:\.\d+)?', s)]
    nums = [n for n in nums if 10 <= n <= 32 and (n * 2) == (n * 2).to_integral()]
    label_m = re.match(r'^\s*(XS|SS|S|M|LL|L|XL|3L|4L)(?![A-Za-z])', s, re.IGNORECASE)
    label = label_m.group(1).upper() if label_m else ''
    if len(nums) >= 2:
        return _fmt_cm(nums[0]), label, f'{_fmt_cm(nums[0])}-{_fmt_cm(nums[-1])}', True
    if len(nums) == 1:
        return _fmt_cm(nums[0]), label, '', True
    return '', s, '', False


# --- メイン生成処理 ---

class InputError(ValueError):
    """利用者に見せるための、入力ファイルに関するエラー"""


def _file_name(f):
    return os.path.basename(getattr(f, 'name', str(f)))


def _read_one_csv(src, encoding='cp932', **kwargs):
    try:
        if hasattr(src, 'seek'):
            src.seek(0)
        return pd.read_csv(src, encoding=encoding, **kwargs)
    except UnicodeDecodeError:
        if hasattr(src, 'seek'):
            src.seek(0)
        return pd.read_csv(src, encoding='utf-8-sig', **kwargs)


def _read_table(f, **kwargs):
    """CSV または CSV入りの zip（NextEngine の SyohinList.zip など）を読む"""
    name = _file_name(f)
    if hasattr(f, 'seek'):
        f.seek(0)
    if name.lower().endswith('.zip') or zipfile.is_zipfile(f):
        if hasattr(f, 'seek'):
            f.seek(0)
        with zipfile.ZipFile(f) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith('.csv') and not n.startswith('__MACOSX')]
            if not csvs:
                raise InputError(f'zipファイル「{name}」の中にCSVが見つかりません。')
            return pd.concat([_read_one_csv(io.BytesIO(z.read(n)), **kwargs) for n in csvs], ignore_index=True)
    return _read_one_csv(f, **kwargs)


def _read_csv_multi(files, encoding='cp932', **kwargs):
    """単一ファイルまたはファイルのリストを受け取り、結合したDataFrameを返す"""
    if not isinstance(files, (list, tuple)):
        files = [files]
    return pd.concat([_read_table(f, encoding=encoding, **kwargs) for f in files], ignore_index=True)


# 入力ファイルの種類と、見分けるための列
FILE_KINDS = {
    'fs_var':   ('FS バリエーションCSV（goodsVariationDetail_*.csv）', {'商品管理番号', 'バリエーション別選択肢（横軸）'}),
    'fs_goods': ('FS 商品情報CSV（goods_*.csv）', {'商品URLコード', '商品説明文'}),
    'ne':       ('NE 商品マスター（SyohinList*.csv / .zip）', {'商品コード', '売価'}),
    'ref':      ('ロコンド出品中データ（commodity_*.csv）', {'品番', 'ロコンド色SKU'}),
}


def identify_file(df):
    cols = set(df.columns)
    for kind, (_, need) in FILE_KINDS.items():
        if need <= cols:
            return kind
    return None


def _load_checked(files, expected, slot_label):
    """読み込んで種類を確認。取り違えていれば分かりやすいエラーにする"""
    if not isinstance(files, (list, tuple)):
        files = [files]
    dfs = []
    for f in files:
        try:
            df = _read_table(f, dtype=str) if expected == 'ref' else _read_table(f)
        except InputError:
            raise
        except Exception as e:
            raise InputError(f'「{slot_label}」のファイル「{_file_name(f)}」を読み込めませんでした（{e}）。'
                             f'{FILE_KINDS[expected][0]} を入れてください。')
        kind = identify_file(df)
        if kind != expected:
            actual = f'{FILE_KINDS[kind][0]} のようです' if kind else '想定しているファイルではないようです'
            raise InputError(f'「{slot_label}」に入れたファイル「{_file_name(f)}」は {actual}。'
                             f'ここには {FILE_KINDS[expected][0]} を入れてください。')
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def locondo_hinban(url_code):
    """FS商品URLコード → ロコンド品番（大文字。'474281-2' のような別ページ枝番は除去）"""
    return re.sub(r'-\d+$', '', _code_str(url_code)).upper()


def locondo_color_code(branch):
    """FS縦軸枝番 → ロコンドのカラーコード（大文字、'-' を '/' に。例: d-si → D/SI）"""
    return _code_str(branch).upper().replace('-', '/')


def _uniq(series):
    return list(dict.fromkeys(series.tolist()))


def generate_locondo_csv(fs_var_file, ne_file, fs_goods_file, season_override: Optional[str] = None,
                         locondo_ref_file=None, infer_category: bool = False,
                         extra_exceptions: Optional[dict] = None) -> GeneratorResult:
    """locondo_ref_file: ロコンド出品中データ（commodity_*.csv）。品番→ブランドの確定に使用
    infer_category: True で商品名からカテゴリーを推定。False（既定）は空欄＝ロコンド側で設定"""
    reference = load_locondo_reference(locondo_ref_file)
    season = (season_override or '').strip().upper()
    if season and not re.fullmatch(r'(SS|AW)\d{4}', season):
        raise InputError(f'シーズンは SS2026 / AW2026 の形式で指定してください（入力値: {season_override}）')

    fs_var = _load_checked(fs_var_file, 'fs_var', '1. FS バリエーションCSV')
    fs_goods = _load_checked(fs_goods_file, 'fs_goods', '2. FS 商品情報CSV')
    ne = _load_checked(ne_file, 'ne', '3. NE 商品マスター')

    fs_var = fs_var.drop_duplicates(subset=['商品管理番号'], keep='first')
    ne = ne.drop_duplicates(subset=['商品コード'], keep='first')  # NE側の重複で行が増えないように
    fs_var['jan_str'] = fs_var['JANコード'].apply(_code_str)
    # JANコードが空のバリエーションはロコンドに登録できないため除外
    jan_empty = fs_var['jan_str'] == ''
    jan_excluded = fs_var.loc[jan_empty, '商品管理番号'].apply(_code_str).tolist()
    fs_var = fs_var[~jan_empty].copy()

    if '商品タグ' not in ne.columns:
        ne['商品タグ'] = ''
    ne = ne.rename(columns={'商品名': 'NE商品名'}) if '商品名' in ne.columns else ne.assign(NE商品名='')
    merged = fs_var.merge(
        ne[['商品コード', '売価', '表示価格', '原価', '商品タグ', 'NE商品名']],
        left_on='商品管理番号', right_on='商品コード', how='left'
    )
    fs_goods = fs_goods.drop_duplicates(subset=['商品URLコード'], keep='first')
    for col in ('下部コメント', 'メイングループ'):
        if col not in fs_goods.columns:
            fs_goods[col] = ''
    merged = merged.merge(
        fs_goods[['商品URLコード', '商品名', '商品説明文', '定価', '下部コメント', 'メイングループ']],
        on='商品URLコード', how='left', suffixes=('', '_goods')
    )
    # 原産国・素材・ワイズは FS の「下部コメント」（[材質] [ワイズ] [生産国]）に書かれていることが多い
    spec_text = [f'{_s(d)}\n{_s(c)}' for d, c in zip(merged['商品説明文'], merged['下部コメント'])]

    skip_mask = merged['売価'].isna()
    skip_count = skip_mask.sum()
    merged = merged[~skip_mask].reset_index(drop=True)

    # NEのタグ・商品名は品番（商品URLコード）単位でまとめて判定（SKU間でブランドがぶれないように）
    hinban = merged['商品URLコード'].apply(locondo_hinban)
    tag_by_hinban = {h: set().union(*[ne_tag_brands(t) for t in grp])
                     for h, grp in merged.groupby(hinban)['商品タグ']}
    ne_name_by_hinban = {h: ' '.join(dict.fromkeys(grp.apply(_s)))
                         for h, grp in merged.groupby(hinban)['NE商品名']}
    brand = [get_brand_info(c, n, d, reference, tag_by_hinban.get(h), ne_name_by_hinban.get(h, ''), grp,
                            extra_exceptions)
             for c, n, d, h, grp in zip(merged['商品URLコード'], merged['商品名_goods'], merged['商品説明文'],
                                        hinban, merged['メイングループ'])]
    ptype = [detect_type_and_category(n) for n in merged['商品名_goods']]
    sizes = [convert_size(s, t[0]) for s, t in zip(merged['バリエーション別選択肢（横軸）'], ptype)]
    mats = [extract_material(d, b[1]) for d, b in zip(spec_text, brand)]

    brand_conflict = pd.Series([b[2] for b in brand], dtype=bool)
    type_uncertain = pd.Series([t[2] for t in ptype], dtype=bool)
    size_ok = pd.Series([s[3] for s in sizes], dtype=bool)
    is_shoe = pd.Series([t[0] == 'シューズ' for t in ptype], dtype=bool)
    prefixes = [b[1] for b in brand]

    color_code = merged['バリエーション別枝番（縦軸）'].apply(locondo_color_code)
    color_name = merged['バリエーション別選択肢（縦軸）'].apply(_s)
    color_code = color_code.where(color_code != '', color_name)
    color_name = color_name.where(color_name != '', color_code)

    retail = [r if pd.notna(r) else s for r, s in zip(merged['定価'], merged['売価'])]
    width = [extract_width(d, p) for d, p in zip(spec_text, prefixes)]

    out = pd.DataFrame(index=merged.index)
    out['カテゴリーコード1']   = [t[1] if infer_category else '' for t in ptype]
    out['カテゴリー1']        = out['カテゴリーコード1'].map(CATEGORY_PATHS).fillna('')
    out['製品種別']           = [t[0] for t in ptype]
    out['ジェンダー']          = 'レディース'
    out['ブランドコード']       = [b[0] for b in brand]
    out['シーズン']            = season
    out['品番']               = hinban
    out['カラーコード']         = color_code
    out['カラー']              = color_code  # ロコンド出品データに合わせてカラーコードを使用（例: BL）
    out['ロコンドカラー']       = [get_locondo_color(n, c) for n, c in zip(color_name, color_code)]
    out['ロコンドサイズ']       = [s[0] for s in sizes]
    out['サイズ']              = [s[1] for s in sizes]
    out['サイズレンジ表記']     = [s[2] for s in sizes]
    out['サイズコード']         = merged['バリエーション別枝番（横軸）'].apply(_code_str)
    out['JANコード']           = merged['jan_str']
    out['ハウスコード']         = merged['商品管理番号'].apply(_code_str)
    out['製品名']              = merged['商品名_goods'].apply(clean_product_name)
    out['定価（税抜き）']       = [to_excl(p) for p in retail]
    out['定価（税込）']         = [to_int(p) for p in retail]
    out['セール価格（税抜き）']  = ''
    out['セール価格（税込）']   = ''
    out['商品説明文']          = merged['商品説明文'].apply(clean_html)
    out['マーケットプレイス']   = 'ロコンド (LD)'
    out['カテゴリー2']         = ''
    out['カテゴリーコード2']    = ''
    out['原産国']              = [extract_origin(t) for t in spec_text]
    out['表素材']              = [m[0] for m in mats]
    out['内側素材']            = '生地・合成繊維'
    # 中敷素材・ソール素材・靴幅はシューズのみの項目
    out['中敷素材']            = ['生地・合成繊維' if s else '' for s in is_shoe]
    out['ソール素材']          = ['合成素材' if s else '' for s in is_shoe]
    out['靴幅']               = [w if s else '' for w, s in zip(width, is_shoe)]
    out['素材詳細']            = [m[1] for m in mats]
    out['洗濯表示']            = ''
    out['寸法詳細']            = ''
    out['備考']               = ''
    out['下代（税抜き）']       = [to_excl(g) if pd.notna(g) and g != 0 else '' for g in merged['原価']]
    out['価格表記タイプ']       = ''
    out['エラー内容']          = ''

    raw_size = merged['バリエーション別選択肢（横軸）'].apply(_s)

    return GeneratorResult(
        df=out[MDS_COLUMNS].reset_index(drop=True),
        success_count=len(out),
        skip_count=int(skip_count),
        brand_missing=_uniq(out.loc[out['ブランドコード'] == '', '品番']),
        brand_conflict=_uniq(out.loc[brand_conflict, '品番']),
        type_uncertain=_uniq(out.loc[type_uncertain, '品番']),
        category_missing=_uniq(out.loc[out['カテゴリーコード1'] == '', '品番']),
        size_invalid=_uniq(out.loc[~size_ok, '品番'] + ':' + raw_size[~size_ok]),
        jan_missing=list(dict.fromkeys(jan_excluded)),
    )
