# app.py
import re
import json
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Amazon 発注推奨 + 画像", layout="wide")

AMAZON_DP = "https://www.amazon.co.jp/dp/{}"

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

def _unescape(s: str) -> str:
    try:
        return bytes(s, "utf-8").decode("unicode_escape").replace("\\/", "/")
    except Exception:
        return s.replace("\\/", "/")

def extract_first_image_url_from_html(html: str) -> str | None:
    # 1) landingImage
    m = re.search(r'"landingImage"\s*:\s*"([^"]+)"', html)
    if m:
        url = _unescape(m.group(1))
        if url.startswith("http"):
            return url

    # 2) hiRes / large
    for key in ["hiRes", "large"]:
        m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html)
        if m:
            url = _unescape(m.group(1))
            if url.startswith("http"):
                return url

    # 3) colorImages blob
    m = re.search(r'"colorImages"\s*:\s*({.*?})\s*,\s*"colorToAsin"', html, re.DOTALL)
    if m:
        blob = _unescape(m.group(1))
        try:
            data = json.loads(blob)
            initial = data.get("initial") or []
            if initial:
                for k in ["hiRes", "large"]:
                    u = initial[0].get(k)
                    if u and isinstance(u, str) and u.startswith("http"):
                        return u
        except Exception:
            pass

    return None

@st.cache_data(show_spinner=False, ttl=60 * 60 * 12)
def get_first_image_url(asin: str) -> str | None:
    url = AMAZON_DP.format(asin)
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        html = r.text
        if "captcha" in html.lower() or "Robot Check" in html:
            return None
        return extract_first_image_url_from_html(html)
    except Exception:
        return None

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

st.title("📦 発注推奨（推奨される在庫補充数量）順 + ASIN画像（1枚目）")

uploaded = st.file_uploader("AmazonからダウンロードしたCSVをアップロード", type=["csv"])
if not uploaded:
    st.info("CSVをアップロードしてください（Amazon在庫のCSV）")
    st.stop()

df = read_inventory_csv(uploaded)

required_cols = ["ASIN", "推奨される在庫補充数量"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"CSVに必要な列が見つかりません: {missing}")
    st.stop()

# 数値化
df["推奨される在庫補充数量"] = pd.to_numeric(df["推奨される在庫補充数量"], errors="coerce").fillna(0).astype(int)

# 文字列列
df["ASIN"] = df["ASIN"].map(normalize_text)
if "Merchant SKU" in df.columns:
    df["Merchant SKU"] = df["Merchant SKU"].map(normalize_text)

# --- UI: フィルタ ---
left, mid, right = st.columns([1.4, 1.1, 1.1], gap="large")

with left:
    query = st.text_input("🔎 SKU または ASIN で検索（部分一致OK）", placeholder="例: B0DG8RNRMX / 7987 / ABC ...")
    st.caption("スペース区切りで複数指定するとAND検索になります。")

with mid:
    only_positive = st.checkbox("発注推奨が0は除外", value=True)
    min_qty = st.number_input("最低発注推奨数", min_value=0, value=1, step=1)

with right:
    max_cards = st.number_input("最大表示件数（画像付き）", min_value=1, max_value=1000, value=120, step=20)
    img_width = st.slider("画像サイズ", min_value=40, max_value=200, value=60, step=10)

view = df.copy()

if only_positive:
    view = view[view["推奨される在庫補充数量"] > 0]
view = view[view["推奨される在庫補充数量"] >= int(min_qty)]

# 検索（SKU or ASIN）
q = (query or "").strip()
if q:
    # 複数語AND検索
    tokens = [t for t in re.split(r"\s+", q) if t]
    # 対象文字列（SKU + ASIN）
    if "Merchant SKU" in view.columns:
        hay = (view["Merchant SKU"].fillna("") + " " + view["ASIN"].fillna("")).str.lower()
    else:
        hay = view["ASIN"].fillna("").str.lower()

    mask = pd.Series(True, index=view.index)
    for t in tokens:
        t = t.lower()
        mask &= hay.str.contains(re.escape(t), na=False)
    view = view[mask]

# 並べ替え
view = view.sort_values("推奨される在庫補充数量", ascending=False).reset_index(drop=True)

st.write(f"表示件数: **{len(view)}**")
st.divider()

# 軽量テーブル（任意）
with st.expander("一覧テーブル（軽量）"):
    show_cols = [c for c in ["Merchant SKU", "商品名", "ASIN", "推奨される在庫補充数量"] if c in view.columns]
    st.dataframe(view[show_cols], use_container_width=True, height=350)

# --- カード表示 ---
view_cards = view.head(int(max_cards))

for _, row in view_cards.iterrows():
    asin = normalize_text(row["ASIN"])
    qty = int(row["推奨される在庫補充数量"])
    sku = normalize_text(row.get("Merchant SKU", ""))
    name = normalize_text(row.get("商品名", ""))
    dp_url = AMAZON_DP.format(asin)

    # 商品カード枠（薄い背景）
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

    col_img, col_info, col_qty = st.columns([0.9, 3.4, 1.2], gap="large")

    with col_img:
        img_url = get_first_image_url(asin)
        if img_url:
            st.image(img_url, width=int(img_width))
        else:
            st.caption("画像取得できません（403/CAPTCHA等）")

    with col_info:
        st.markdown(f"**ASIN:** [{asin}]({dp_url})")
        if sku:
            st.markdown(f"**SKU:** `{sku}`")
        if name:
            st.caption(name)

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

    # カード閉じ
    st.markdown("</div>", unsafe_allow_html=True)

    # 商品ごとの区切り線
    st.divider()
