# app.py
import re
import json
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="楽天画像 + 発注推奨", layout="wide")

RAKUTEN_ITEM = "https://item.rakuten.co.jp/hype/{}/"

# それっぽくブラウザに見せる（弾かれにくくする）
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
}

def read_inventory_csv(uploaded_file) -> pd.DataFrame:
    # Amazonの在庫系CSVはcp932(Shift-JIS)が多い
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

    head = sku.split("X")[0]  # Xより前
    m = re.search(r"(\d{7})", head)
    if m:
        return m.group(1)

    # 念のため全体からも拾う
    m2 = re.search(r"(\d{7})", sku)
    return m2.group(1) if m2 else None

def extract_rakuten_image_url_from_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")

    # 1) og:image（最優先）
    meta = soup.find("meta", attrs={"property": "og:image"})
    if meta and meta.get("content", "").startswith("http"):
        return meta["content"]

    # 2) JSON-LD（構造化データ）から拾う
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(s.get_text(strip=True))

            candidates = data if isinstance(data, list) else [data]
            for d in candidates:
                img = d.get("image")
                if isinstance(img, str) and img.startswith("http"):
                    return img
                if isinstance(img, list) and img and isinstance(img[0], str) and img[0].startswith("http"):
                    return img[0]
        except Exception:
            continue

    return None

@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)  # 24hキャッシュ
def get_rakuten_image_url_by_code(code7: str) -> str | None:
    if not code7:
        return None
    url = RAKUTEN_ITEM.format(code7)
    try:
        r = requests.get(
            url,
            headers={
                **DEFAULT_HEADERS,
                "Referer": "https://item.rakuten.co.jp/",
            },
            timeout=15,
        )
        if r.status_code != 200:
            return None

        return extract_rakuten_image_url_from_html(r.text)
    except Exception:
        return None

# ---------------- UI ----------------

st.title("📦 発注推奨（推奨される在庫補充数量）順 + 楽天画像（1枚目）")
st.caption("画像は楽天（item.rakuten.co.jp/hype/7桁/）のみから取得します。")

uploaded = st.file_uploader("CSVをアップロード（Amazonからダウンロードした在庫CSVなど）", type=["csv"])
if not uploaded:
    st.info("CSVをアップロードしてください")
    st.stop()

df = read_inventory_csv(uploaded)

required_cols = ["ASIN", "推奨される在庫補充数量"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"CSVに必要な列が見つかりません: {missing}")
    st.stop()

# 数値化
df["推奨される在庫補充数量"] = pd.to_numeric(df["推奨される在庫補充数量"], errors="coerce").fillna(0).astype(int)

# 文字列正規化
df["ASIN"] = df["ASIN"].map(normalize_text)
if "Merchant SKU" in df.columns:
    df["Merchant SKU"] = df["Merchant SKU"].map(normalize_text)

# ---- フィルタUI ----
left, mid, right = st.columns([1.6, 1.1, 1.3], gap="large")

with left:
    query = st.text_input("🔎 SKU または ASIN で検索（部分一致OK）", placeholder="例: 7987482 / B0DG... / 7987 ...")
    st.caption("スペース区切りで複数指定すると AND 検索になります。")

with mid:
    only_positive = st.checkbox("発注推奨が0は除外", value=True)
    min_qty = st.number_input("最低発注推奨数", min_value=0, value=1, step=1)

with right:
    max_cards = st.number_input("最大表示件数（画像付き）", min_value=1, max_value=1000, value=150, step=25)
    img_width = st.slider("画像サイズ", min_value=40, max_value=200, value=60, step=10)

view = df.copy()

if only_positive:
    view = view[view["推奨される在庫補充数量"] > 0]
view = view[view["推奨される在庫補充数量"] >= int(min_qty)]

# 検索（SKU or ASIN）
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

# 並べ替え（多い順）
view = view.sort_values("推奨される在庫補充数量", ascending=False).reset_index(drop=True)

st.write(f"表示件数: **{len(view)}**")
st.divider()

# 軽量テーブル
with st.expander("一覧テーブル（軽量）"):
    show_cols = [c for c in ["Merchant SKU", "商品名", "ASIN", "推奨される在庫補充数量"] if c in view.columns]
    st.dataframe(view[show_cols], use_container_width=True, height=350)

# ---- カード表示 ----
view_cards = view.head(int(max_cards))

for _, row in view_cards.iterrows():
    asin = normalize_text(row["ASIN"])
    qty = int(row["推奨される在庫補充数量"])
    sku = normalize_text(row.get("Merchant SKU", ""))
    name = normalize_text(row.get("商品名", ""))

    code7 = extract_7digits_from_sku(sku)
    rakuten_url = RAKUTEN_ITEM.format(code7) if code7 else None

    # 商品カード枠
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
        img_url = get_rakuten_image_url_by_code(code7) if code7 else None
        if img_url:
            st.image(img_url, width=int(img_width))
        else:
            st.caption("画像なし")

    with col_info:
        if sku:
            st.markdown(f"**SKU:** `{sku}`")
        st.markdown(f"**ASIN:** `{asin}`")
        if name:
            st.caption(name)
        if rakuten_url:
            st.markdown(f"**楽天:** {rakuten_url}")
        else:
            st.caption("楽天URL生成不可（SKUから7桁抽出できず）")

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
