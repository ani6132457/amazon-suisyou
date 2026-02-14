# app.py
import os
import re
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

st.set_page_config(page_title="楽天画像 + 発注推奨（高速・永続キャッシュ）", layout="wide")

RAKUTEN_ITEM = "https://item.rakuten.co.jp/hype/{}/"
CACHE_FILE = "image_cache.csv"

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
    if m:
        return m.group(1)
    return None

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
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument("--lang=ja-JP")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(20)
    return driver

def extract_img(html, base_url):
    soup = BeautifulSoup(html, "lxml")
    span = soup.find("span", class_="sale_desc")
    if not span:
        return None
    img = span.find("img")
    if not img:
        return None
    src = img.get("src")
    if not src:
        return None
    return urljoin(base_url, src)

def fetch_image(url):
    driver = get_driver()
    try:
        driver.get(url)
        html = driver.page_source
        return extract_img(html, driver.current_url)
    except:
        return None

# ---------------- UI ----------------
st.title("📦 発注推奨順 + 楽天画像（高速表示 + 永続キャッシュ）")

uploaded = st.file_uploader("CSVをアップロード", type=["csv"])
if not uploaded:
    st.stop()

df = read_inventory_csv(uploaded)

required = ["ASIN", "推奨される在庫補充数量"]
for col in required:
    if col not in df.columns:
        st.error(f"{col} が見つかりません")
        st.stop()

df["推奨される在庫補充数量"] = pd.to_numeric(
    df["推奨される在庫補充数量"], errors="coerce"
).fillna(0).astype(int)

if "Merchant SKU" in df.columns:
    df["Merchant SKU"] = df["Merchant SKU"].map(normalize)

df = df.sort_values("推奨される在庫補充数量", ascending=False).reset_index(drop=True)

# 楽天URL生成
def build_url(row):
    sku = row.get("Merchant SKU", "")
    code = extract_7digits(sku)
    if code:
        return RAKUTEN_ITEM.format(code)
    return ""

df["rakuten_url"] = df.apply(build_url, axis=1)

# 永続キャッシュ読み込み
cache_df = load_cache()
cache_dict = dict(zip(cache_df["rakuten_url"], cache_df["image_url"]))

# 表示件数
max_rows = st.number_input("表示件数", 50, 2000, 200, 50)
img_size = st.slider("画像サイズ", 30, 120, 45)

rows = df.head(int(max_rows))

# ---------------- 先に文字を即表示 ----------------
containers = []
for idx, row in rows.iterrows():
    c = st.container()
    containers.append((c, row))

# ---------------- 画像取得（自動実行） ----------------
driver = get_driver()

for container, row in containers:
    with container:
        sku = row.get("Merchant SKU", "")
        asin = row.get("ASIN", "")
        qty = row.get("推奨される在庫補充数量", 0)
        url = row.get("rakuten_url", "")

        col1, col2, col3 = st.columns([0.4, 3.5, 1])

        # ---- 画像 ----
        with col1:
            img_url = cache_dict.get(url)

            if img_url:
                st.image(img_url, width=img_size)
            else:
                st.caption("取得中...")
                if url:
                    new_img = fetch_image(url)
                    if new_img:
                        cache_dict[url] = new_img
                        cache_df.loc[len(cache_df)] = [url, new_img]
                        save_cache(cache_df)
                        st.image(new_img, width=img_size)
                    else:
                        st.caption("なし")

        # ---- 情報（即表示）----
        with col2:
            st.markdown(f"**{sku}**  |  ASIN: {asin}")

        # ---- 発注推奨 ----
        with col3:
            st.markdown(
                f"""
                <div style="
                    border-radius:8px;
                    padding:6px;
                    text-align:center;
                    background:rgba(255,0,0,0.08);
                ">
                <div style="font-size:11px;">発注推奨</div>
                <div style="font-size:20px;font-weight:900;color:#d40000;">
                {qty}
                </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.divider()
