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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

st.set_page_config(page_title="楽天画像 + 発注推奨（Selenium/Cloud対応）", layout="wide")

RAKUTEN_ITEM = "https://item.rakuten.co.jp/hype/{}/"

# ---------- CSV ----------
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
    if not sku:
        return None
    sku = str(sku).strip()
    head = sku.split("X")[0]
    m = re.search(r"(\d{7})", head)
    if m:
        return m.group(1)
    m2 = re.search(r"(\d{7})", sku)
    return m2.group(1) if m2 else None

# ---------- HTML parse ----------
def extract_img_from_sale_desc(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    span = soup.find("span", class_="sale_desc")
    if not span:
        return None
    img = span.find("img")
    if not img:
        return None
    src = (img.get("src") or "").strip()
    if not src:
        return None
    return urljoin(base_url, src)

# ---------- Selenium (Cloud-ready) ----------
def detect_chrome_binary() -> str:
    """
    Streamlit Cloud(Linux)では /usr/bin/chromium が多い。
    ローカルWindows/Macでも動かせるよう複数候補を見る。
    """
    candidates = [
        os.environ.get("CHROME_BINARY", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    # 見つからない場合は空（webdriver側が探せる環境もある）
    return ""

def detect_chromedriver_path() -> str:
    candidates = [
        os.environ.get("CHROMEDRIVER_PATH", ""),
        "/usr/bin/chromedriver",
        "/usr/lib/chromium/chromedriver",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""

@st.cache_resource
def get_driver():
    chrome_bin = detect_chrome_binary()
    chromedriver_path = detect_chromedriver_path()

    opts = Options()
    # Cloudでは headless 必須
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument("--lang=ja-JP")

    # UAをブラウザっぽく
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )

    if chrome_bin:
        opts.binary_location = chrome_bin

    # Serviceでchromedriverを明示
    if chromedriver_path:
        service = Service(executable_path=chromedriver_path)
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        # 環境によっては自動検出できる場合もある
        driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(30)
    return driver

@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def fetch_rakuten_image_by_url(url: str) -> dict:
    if not url:
        return {"img_url": None, "status": "URLなし", "final_url": "", "title": ""}

    driver = get_driver()

    try:
        driver.get(url)

        # sale_descが出るまで待つ（出なければそのまま解析）
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "span.sale_desc"))
            )
        except Exception:
            pass

        final_url = driver.current_url
        html = driver.page_source
        title = driver.title or ""

        # WAF/ブロックページ
        if "Reference #" in html or "Access Denied" in html:
            return {"img_url": None, "status": "ブロック（Reference/Denied）", "final_url": final_url, "title": title}

        img_url = extract_img_from_sale_desc(html, base_url=final_url)
        if img_url:
            return {"img_url": img_url, "status": "OK", "final_url": final_url, "title": title}
        else:
            return {"img_url": None, "status": "sale_desc/imgなし", "final_url": final_url, "title": title}

    except Exception as e:
        return {"img_url": None, "status": f"ERROR: {type(e).__name__}", "final_url": "", "title": ""}

def choose_page_url(row: pd.Series, url_colname: str | None) -> str | None:
    if url_colname and url_colname in row.index:
        u = normalize_text(row.get(url_colname, ""))
        if u.startswith("http"):
            return u
    sku = normalize_text(row.get("Merchant SKU", ""))
    code7 = extract_7digits_from_sku(sku)
    if code7:
        return RAKUTEN_ITEM.format(code7)
    return None

# ---------- UI ----------
st.title("📦 発注推奨順 + 楽天画像（Selenium / Streamlit Cloud対応）")

uploaded = st.file_uploader("CSVをアップロード", type=["csv"])
if not uploaded:
    st.stop()

df = read_inventory_csv(uploaded)

required_cols = ["ASIN", "推奨される在庫補充数量"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"CSVに必要な列が見つかりません: {missing}")
    st.stop()

df["推奨される在庫補充数量"] = pd.to_numeric(df["推奨される在庫補充数量"], errors="coerce").fillna(0).astype(int)
df["ASIN"] = df["ASIN"].map(normalize_text)
if "Merchant SKU" in df.columns:
    df["Merchant SKU"] = df["Merchant SKU"].map(normalize_text)

url_candidates = [c for c in df.columns if "url" in c.lower() or "URL" in c or "Url" in c]
url_colname = None
if url_candidates:
    url_colname = st.selectbox("（任意）取得元URL列（VBAのC列相当）", ["(使わない)"] + url_candidates, index=0)
    if url_colname == "(使わない)":
        url_colname = None

left, mid, right = st.columns([1.6, 1.1, 1.3], gap="large")
with left:
    query = st.text_input("🔎 SKU または ASIN で検索（部分一致OK）", placeholder="例: 7987070 / B0DG... / 7987 ...")
    st.caption("スペース区切りで複数指定すると AND 検索になります。")
with mid:
    only_positive = st.checkbox("発注推奨が0は除外", value=True)
    min_qty = st.number_input("最低発注推奨数", min_value=0, value=1, step=1)
with right:
    max_cards = st.number_input("最大表示件数", min_value=1, max_value=2000, value=150, step=50)
    img_width = st.slider("画像サイズ", min_value=30, max_value=200, value=60, step=10)

debug = st.checkbox("デバッグ表示", value=False)

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

view = view.sort_values("推奨される在庫補充数量", ascending=False).reset_index(drop=True)

st.write(f"表示件数: **{len(view)}**")
st.divider()

for _, row in view.head(int(max_cards)).iterrows():
    asin = normalize_text(row["ASIN"])
    qty = int(row["推奨される在庫補充数量"])
    sku = normalize_text(row.get("Merchant SKU", ""))
    name = normalize_text(row.get("商品名", ""))

    page_url = choose_page_url(row, url_colname=url_colname)
    res = fetch_rakuten_image_by_url(page_url) if page_url else {"img_url": None, "status": "URL生成不可", "final_url": "", "title": ""}

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
        if res["img_url"]:
            st.image(res["img_url"], width=int(img_width))
        else:
            st.caption(f"画像なし\n({res['status']})")

    with col_info:
        if sku:
            st.markdown(f"**SKU:** `{sku}`")
        st.markdown(f"**ASIN:** `{asin}`")
        if name:
            st.caption(name)
        if page_url:
            st.markdown(f"**取得元URL:** {page_url}")
        if debug:
            st.caption(f"title: {res.get('title','')} / final_url: {res.get('final_url','')}")

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
