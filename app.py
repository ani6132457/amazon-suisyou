# app.py
import re
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from urllib.parse import urljoin

st.set_page_config(page_title="発注推奨 + 楽天画像（sale_desc方式）", layout="wide")

# SKUの7桁 → 楽天URL（あなた指定）
RAKUTEN_ITEM = "https://item.rakuten.co.jp/hype/{}/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://item.rakuten.co.jp/",
}

# ----------------- utilities -----------------
def read_inventory_csv(uploaded_file) -> pd.DataFrame:
    try:
        return pd.read_csv(uploaded_file, encoding="cp932")
    except Exception:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding="utf-8")

def normalize_text(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()

def extract_7digits_from_sku(sku: str) -> str | None:
    """
    SKUの 'X' より前の部分から7桁の数字を取り出す想定。
    例: 7987482X11Y11 -> 7987482
    """
    if not sku:
        return None
    sku = str(sku).strip()

    head = sku.split("X")[0]
    m = re.search(r"(\d{7})", head)
    if m:
        return m.group(1)

    m2 = re.search(r"(\d{7})", sku)
    return m2.group(1) if m2 else None

# ----------------- VBA互換：sale_desc内のimg src -----------------
def extract_img_from_sale_desc(html: str, base_url: str) -> str | None:
    """
    VBAのロジックと同じ：
    <span class="sale_desc"> を探して、その中の最初の <img> の src を返す。
    BeautifulSoupで安全にやる版。
    """
    soup = BeautifulSoup(html, "lxml")

    span = soup.find("span", class_="sale_desc")
    if not span:
        return None

    img = span.find("img")
    if not img:
        return None

    src = img.get("src") or ""
    src = src.strip()
    if not src:
        return None

    # 相対パスの場合に備えて絶対URL化
    return urljoin(base_url, src)

@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_image_url_from_page(url: str) -> tuple[str | None, str]:
    """
    URLからHTMLを取得して sale_desc -> img src を抽出。
    戻り値: (img_url or None, status_message)
    """
    if not url:
        return None, "URLなし"

    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=20)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"

        # 文字化け対策（requestsが誤判定することがある）
        # まず apparent_encoding を使いつつ、壊れる場合はそのまま
        try:
            r.encoding = r.apparent_encoding
        except Exception:
            pass

        html = r.text

        img_url = extract_img_from_sale_desc(html, base_url=url)
        if img_url:
            return img_url, "OK"
        else:
            return None, "sale_descなし/ imgなし"
    except Exception as e:
        return None, f"ERROR: {type(e).__name__}"

def choose_page_url(row: pd.Series, url_colname: str | None) -> str | None:
    """
    画像取得元URLを決める。
    1) CSVにURL列があるならそれを優先（VBAと同じ運用）
    2) なければSKUから7桁を作って楽天URLを生成
    """
    if url_colname and url_colname in row.index:
        u = normalize_text(row.get(url_colname, ""))
        if u.startswith("http"):
            return u

    sku = normalize_text(row.get("Merchant SKU", ""))
    code7 = extract_7digits_from_sku(sku)
    if code7:
        return RAKUTEN_ITEM.format(code7)

    return None

# ----------------- UI -----------------
st.title("📦 発注推奨順 + 画像URL取得（VBA互換: sale_desc→img src）")
st.caption("画像はURL先HTMLの `<span class='sale_desc'>` 内の最初の `<img src>` を取得します（VBAと同じ方式）。")

uploaded = st.file_uploader("CSVをアップロード", type=["csv"])
if not uploaded:
    st.stop()

df = read_inventory_csv(uploaded)

# 必須列
required_cols = ["ASIN", "推奨される在庫補充数量"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"CSVに必要な列が見つかりません: {missing}")
    st.stop()

df["推奨される在庫補充数量"] = pd.to_numeric(df["推奨される在庫補充数量"], errors="coerce").fillna(0).astype(int)

# 文字列列
df["ASIN"] = df["ASIN"].map(normalize_text)
if "Merchant SKU" in df.columns:
    df["Merchant SKU"] = df["Merchant SKU"].map(normalize_text)

# URL列をユーザーに選ばせる（あれば）
url_candidates = [c for c in df.columns if "url" in c.lower() or "URL" in c or "Url" in c]
url_colname = None
if url_candidates:
    url_colname = st.selectbox("（任意）画像取得元URLの列を選択（VBAのC列に相当）", ["(使わない)"] + url_candidates, index=0)
    if url_colname == "(使わない)":
        url_colname = None
else:
    st.info("CSV内にURLっぽい列が見当たりませんでした。SKU→7桁→楽天URL生成で取得します。")

# フィルタ
left, mid, right = st.columns([1.6, 1.1, 1.3], gap="large")
with left:
    query = st.text_input("🔎 SKU または ASIN で検索（部分一致OK）", placeholder="例: 7987482 / B0DG... / 7987 ...")
    st.caption("スペース区切りで複数指定すると AND 検索になります。")
with mid:
    only_positive = st.checkbox("発注推奨が0は除外", value=True)
    min_qty = st.number_input("最低発注推奨数", min_value=0, value=1, step=1)
with right:
    max_cards = st.number_input("最大表示件数", min_value=1, max_value=2000, value=200, step=50)
    img_width = st.slider("画像サイズ", min_value=30, max_value=200, value=60, step=10)

view = df.copy()
if only_positive:
    view = view[view["推奨される在庫補充数量"] > 0]
view = view[view["推奨される在庫補充数量"] >= int(min_qty)]

q = (query or "").strip()
if q:
    tokens = [t for t in re.split(r"\s+", q) if t]
    if "Merchant SKU" in view.columns:
        hay = (view["Merchant SKU"].fillna("") + " " + view["ASIN"].fillna("")).str.lower()
    else:
        hay = view["ASIN"].fillna("").str.lower()

    mask = pd.Series(True, index=view.index)
    for t in tokens:
        t = t.lower()
        mask &= hay.str.contains(re.escape(t), na=False)
    view = view[mask]

# 並び替え
view = view.sort_values("推奨される在庫補充数量", ascending=False).reset_index(drop=True)

st.write(f"表示件数: **{len(view)}**")
st.divider()

# 一覧テーブル（軽量）
with st.expander("一覧テーブル（軽量）"):
    show_cols = [c for c in ["Merchant SKU", "商品名", "ASIN", "推奨される在庫補充数量"] if c in view.columns]
    st.dataframe(view[show_cols], use_container_width=True, height=320)

# カード表示
view_cards = view.head(int(max_cards))

for _, row in view_cards.iterrows():
    asin = normalize_text(row["ASIN"])
    qty = int(row["推奨される在庫補充数量"])
    sku = normalize_text(row.get("Merchant SKU", ""))
    name = normalize_text(row.get("商品名", ""))

    page_url = choose_page_url(row, url_colname=url_colname)
    img_url, status = get_image_url_from_page(page_url) if page_url else (None, "URL生成不可")

    st.markdown(
        """
        <div style="
            border: 1px solid rgba(0,0,0,0.08);
            border-radius: 14px;
            padding: 12px 12px 6px 12px;
            background: rgba(0,0,0,0.015);
        ">
        """,
        unsafe_allow_html=True,
    )

    col_img, col_info, col_qty = st.columns([0.6, 3.8, 1.2], gap="medium")

    with col_img:
        if img_url:
            st.image(img_url, width=int(img_width))
        else:
            st.caption(f"画像なし\n({status})")

    with col_info:
        if sku:
            st.markdown(f"**SKU:** `{sku}`")
        st.markdown(f"**ASIN:** `{asin}`")
        if name:
            st.caption(name)
        if page_url:
            st.markdown(f"**取得元URL:** {page_url}")
        else:
            st.caption("取得元URLを作れませんでした（SKUから7桁抽出失敗 or URL列未指定）")

    with col_qty:
        st.markdown(
            f"""
            <div style="
                border-radius: 14px;
                padding: 12px 10px;
                border: 1px solid rgba(255,0,0,0.25);
                background: rgba(255,0,0,0.07);
                text-align: center;
            ">
                <div style="font-size: 12px; opacity: 0.75;">発注推奨</div>
                <div style="font-size: 36px; font-weight: 900; color: #d40000; line-height: 1.05;">
                    {qty}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)
    st.divider()
