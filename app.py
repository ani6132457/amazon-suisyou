# app.py
import os
import re
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

st.set_page_config(page_title="楽天画像 + 発注推奨", layout="wide")

RAKUTEN_ITEM = "https://item.rakuten.co.jp/hype/{}/"
CACHE_FILE = "image_cache.csv"

# ----- 超コンパクトCSS -----
st.markdown("""
<style>
.block-container {padding-top: 0.4rem; padding-bottom: 0.4rem;}
div[data-testid="stVerticalBlock"] {gap: 0.15rem;}
div[data-testid="stMarkdown"] p {margin:0;}
hr {margin:0.25rem 0;}
</style>
""", unsafe_allow_html=True)

# ---------------- CSV ----------------
def read_inventory_csv(uploaded_file):
    try:
        return pd.read_csv(uploaded_file, encoding="cp932")
    except:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding="utf-8")

def normalize(x):
    if pd.isna(x):
        return ""
    return str(x).strip()

def extract_7digits(sku):
    if not sku:
        return None
    sku = str(sku).strip()
    head = sku.split("X")[0]
    m = re.search(r"(\d{7})", head)
    return m.group(1) if m else None

def extract_color(name):
    if not name:
        return ""
    m = re.search(r"[（(](.*?)[）)]", name)
    return m.group(1) if m else ""

# ---------------- 永続キャッシュ ----------------
def load_cache():
    if os.path.exists(CACHE_FILE):
        return pd.read_csv(CACHE_FILE)
    return pd.DataFrame(columns=["rakuten_url", "image_url"])

def save_cache(df):
    df.to_csv(CACHE_FILE, index=False)

# ---------------- Selenium ----------------
@st.cache_resource
def get_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    )
    return webdriver.Chrome(options=opts)

def extract_img(html, base_url):
    soup = BeautifulSoup(html, "lxml")
    span = soup.find("span", class_="sale_desc")
    if not span:
        return None
    img = span.find("img")
    if not img:
        return None
    return urljoin(base_url, img.get("src"))

def fetch_image(url):
    driver = get_driver()
    try:
        driver.get(url)
        return extract_img(driver.page_source, driver.current_url)
    except:
        return None

# ---------------- UI ----------------
st.title("📦 発注推奨順")

uploaded = st.file_uploader("CSVをアップロード", type=["csv"])
if not uploaded:
    st.stop()

df = read_inventory_csv(uploaded)

# 必須列
df["推奨される在庫補充数量"] = pd.to_numeric(
    df["推奨される在庫補充数量"], errors="coerce"
).fillna(0).astype(int)

df["ASIN"] = df["ASIN"].map(normalize)

if "Merchant SKU" in df.columns:
    df["Merchant SKU"] = df["Merchant SKU"].map(normalize)

if "商品名" in df.columns:
    df["商品名"] = df["商品名"].map(normalize)
else:
    df["商品名"] = ""

df = df.sort_values("推奨される在庫補充数量", ascending=False)

# -------- 検索 --------
search = st.text_input("🔎 SKU / ASIN / 商品名 検索（部分一致）")

if search:
    search = search.lower()
    mask = (
        df["ASIN"].str.lower().str.contains(search, na=False)
        | df["Merchant SKU"].str.lower().str.contains(search, na=False)
        | df["商品名"].str.lower().str.contains(search, na=False)
    )
    df = df[mask]

# 楽天URL生成
def build_url(row):
    code = extract_7digits(row["Merchant SKU"])
    if code:
        return RAKUTEN_ITEM.format(code)
    return ""

df["rakuten_url"] = df.apply(build_url, axis=1)

# キャッシュ読み込み
cache_df = load_cache()
cache_dict = dict(zip(cache_df["rakuten_url"], cache_df["image_url"]))

# 表示件数
max_rows = st.number_input("表示件数", 50, 2000, 300, 50)
img_size = st.slider("画像サイズ", 25, 70, 35)

rows = df.head(int(max_rows))

driver = get_driver()

for _, row in rows.iterrows():

    sku = row["Merchant SKU"]
    asin = row["ASIN"]
    name = row["商品名"]
    qty = row["推奨される在庫補充数量"]
    url = row["rakuten_url"]

    color = extract_color(name)

    col1, col2, col3 = st.columns([0.32, 4, 0.8])

    # ---- 画像 ----
with col1:
    img_url = cache_dict.get(url)

    if img_url:
        st.markdown(
            f"""
            <div style="
                width:{img_size}px;
                height:{img_size}px;
                display:flex;
                align-items:center;
                justify-content:center;
                overflow:hidden;
                border-radius:4px;
            ">
                <img src="{img_url}"
                     style="
                         max-width:100%;
                         max-height:100%;
                         object-fit:contain;
                     ">
            </div>
            """,
            unsafe_allow_html=True
        )
        else:
            if url:
                new_img = fetch_image(url)
                if new_img:
                    cache_dict[url] = new_img
                    cache_df.loc[len(cache_df)] = [url, new_img]
                    save_cache(cache_df)
                    st.image(new_img, width=img_size)
                else:
                    st.caption("—")

    # ---- SKU / ASIN / カラー ----
    with col2:
        line = f"SKU:{sku} | ASIN:{asin}"
        if color:
            line += f" | <b>{color}</b>"
        st.markdown(line, unsafe_allow_html=True)

    # ---- 発注推奨 ----
    with col3:
        st.markdown(
            f"""
            <div style="
                padding:4px;
                text-align:center;
                background:rgba(255,0,0,0.12);
                border-radius:6px;">
            <div style="font-size:9px;">発注</div>
            <div style="font-size:17px;font-weight:900;color:#d40000;">
            {qty}
            </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<hr>", unsafe_allow_html=True)
