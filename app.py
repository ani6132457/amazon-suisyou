# app.py
import re
import json
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="Amazon 発注推奨 + 画像", layout="wide")

AMAZON_DP = "https://www.amazon.co.jp/dp/{}"

# それっぽく人間のブラウザに見せる（403回避に多少効く）
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
    # AmazonのHTML内JSONは \" や \u0026 が混ざることがあるので軽く戻す
    try:
        return bytes(s, "utf-8").decode("unicode_escape").replace("\\/", "/")
    except Exception:
        return s.replace("\\/", "/")

def extract_first_image_url_from_html(html: str) -> str | None:
    """
    Amazon商品ページHTMLから「1枚目の画像URL」っぽいものを抽出する。
    取れない時は None。
    """

    # 1) landingImage（比較的安定）
    m = re.search(r'"landingImage"\s*:\s*"([^"]+)"', html)
    if m:
        url = _unescape(m.group(1))
        if url.startswith("http"):
            return url

    # 2) hiRes / large のURLを拾う（colorImages系）
    # hiRes が空のことがあるので large も見る
    for key in ["hiRes", "large"]:
        m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html)
        if m:
            url = _unescape(m.group(1))
            if url.startswith("http"):
                return url

    # 3) ImageBlockATF / scripts 内の JSON から拾う（当たりやすいが変動もする）
    # "colorImages": {"initial":[{...}]} の中の hiRes/large を優先
    m = re.search(r'"colorImages"\s*:\s*({.*?})\s*,\s*"colorToAsin"', html, re.DOTALL)
    if m:
        blob = m.group(1)
        blob = _unescape(blob)
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

@st.cache_data(show_spinner=False, ttl=60 * 60 * 12)  # 12時間キャッシュ
def get_first_image_url(asin: str) -> str | None:
    url = AMAZON_DP.format(asin)
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code != 200:
            return None

        html = r.text

        # CAPTCHA っぽい時は諦め
        if "captcha" in html.lower() or "Robot Check" in html:
            return None

        return extract_first_image_url_from_html(html)
    except Exception:
        return None

def read_inventory_csv(uploaded_file) -> pd.DataFrame:
    # Amazonの在庫系CSVはcp932が多い
    # だめならutf-8も試す
    try:
        return pd.read_csv(uploaded_file, encoding="cp932")
    except Exception:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding="utf-8")

st.title("📦 発注推奨（推奨される在庫補充数量）順 + ASIN画像（1枚目）")

uploaded = st.file_uploader("AmazonからダウンロードしたCSVをアップロード", type=["csv"])
if not uploaded:
    st.info("CSVをアップロードしてください（Amazon在庫のCSV）")
    st.stop()

df = read_inventory_csv(uploaded)

# 必須列チェック（あなたのCSVはこの列名でOK）
required_cols = ["ASIN", "推奨される在庫補充数量"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"CSVに必要な列が見つかりません: {missing}")
    st.stop()

# 推奨数量を数値化
df["推奨される在庫補充数量"] = pd.to_numeric(df["推奨される在庫補充数量"], errors="coerce").fillna(0).astype(int)

# フィルタ
min_qty = st.slider("最低表示する発注推奨数", 0, int(df["推奨される在庫補充数量"].max() if len(df) else 0), 1)
only_positive = st.checkbox("発注推奨が0の行は除外", value=True)

view = df.copy()
if only_positive:
    view = view[view["推奨される在庫補充数量"] > 0]
view = view[view["推奨される在庫補充数量"] >= min_qty]

# 並べ替え（多い順）
view = view.sort_values("推奨される在庫補充数量", ascending=False).reset_index(drop=True)

st.write(f"表示件数: **{len(view)}**")

# 表も欲しい場合（軽く）
with st.expander("一覧テーブル（軽量）"):
    show_cols = [c for c in ["Merchant SKU", "商品名", "ASIN", "推奨される在庫補充数量"] if c in view.columns]
    st.dataframe(view[show_cols], use_container_width=True, height=350)

st.divider()

# カード表示（画像 + 発注推奨を大きく色付き）
max_cards = st.number_input("最大表示件数（画像付きは重いので調整可）", min_value=1, max_value=500, value=80, step=10)
view_cards = view.head(int(max_cards))

for i, row in view_cards.iterrows():
    asin = str(row["ASIN"]).strip()
    qty = int(row["推奨される在庫補充数量"])
    sku = str(row.get("Merchant SKU", "")).strip()
    name = str(row.get("商品名", "")).strip()

    dp_url = AMAZON_DP.format(asin)

    col_img, col_info, col_qty = st.columns([1.1, 3.2, 1.2], gap="large")

    with col_img:
        img_url = get_first_image_url(asin)
        if img_url:
            st.image(img_url, use_container_width=True)
        else:
            st.caption("画像取得できません（403/CAPTCHA等）")

    with col_info:
        st.markdown(f"**ASIN:** [{asin}]({dp_url})")
        if sku:
            st.markdown(f"**SKU:** `{sku}`")
        if name:
            st.caption(name)

    with col_qty:
        # qty を強調（大きく・色付き）
        # 数が大きいほど目立たせたいならここで条件分岐もOK
        st.markdown(
            f"""
            <div style="
                border-radius: 14px;
                padding: 14px 12px;
                border: 1px solid rgba(255,0,0,0.25);
                background: rgba(255,0,0,0.06);
                text-align: center;
            ">
                <div style="font-size: 12px; opacity: 0.75;">発注推奨</div>
                <div style="font-size: 40px; font-weight: 800; color: #d40000; line-height: 1.1;">
                    {qty}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")  # 余白
