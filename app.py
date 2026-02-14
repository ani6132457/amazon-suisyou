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

st.set_page_config(page_title="楽天画像 + 発注推奨（高速UI）", layout="wide")

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

    if chrome_bin:
        opts.binary_location = chrome_bin

    if chromedriver_path:
        service = Service(executable_path=chromedriver_path)
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(30)
    return driver

@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def fetch_rakuten_image_by_url(url: str) -> dict:
    if not url:
        return {"img_url": None, "status": "URLなし"}

    driver = get_driver()
    try:
        driver.get(url)

        try:
            WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "span.sale_desc"))
            )
        except Exception:
            pass

        html = driver.page_source
        final_url = driver.current_url

        if "Reference #" in html or "Access Denied" in html:
            return {"img_url": None, "status": "ブロック"}

        img_url = extract_img_from_sale_desc(html, base_url=final_url)
        if img_url:
            return {"img_url": img_url, "status": "OK"}
        return {"img_url": None, "status": "imgなし"}

    except Exception as e:
        return {"img_url": None, "status": f"ERROR:{type(e).__name__}"}

# ---------- URL決定 ----------
def choose_page_url_from_row(row: pd.Series, url_colname: str | None) -> str | None:
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
st.title("📦 発注推奨順（即表示） + 画像（あとから取得）")

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

# URL列候補
url_candidates = [c for c in df.columns if "url" in c.lower() or "URL" in c or "Url" in c]
url_colname = None
if url_candidates:
    url_colname = st.selectbox("（任意）取得元URL列（VBAのC列相当）", ["(使わない)"] + url_candidates, index=0)
    if url_colname == "(使わない)":
        url_colname = None

# フィルタ
left, mid, right = st.columns([1.6, 1.1, 1.3], gap="large")
with left:
    query = st.text_input("🔎 SKU または ASIN で検索（部分一致OK）", placeholder="例: 7987070 / B0DG... / 7987 ...")
    st.caption("スペース区切りで複数指定すると AND 検索になります。")
with mid:
    only_positive = st.checkbox("発注推奨が0は除外", value=True)
    min_qty = st.number_input("最低発注推奨数", min_value=0, value=1, step=1)
with right:
    show_rows = st.number_input("一覧表示件数（先に表示）", min_value=20, max_value=5000, value=300, step=50)

# 表示密度（小さく）
dense = st.checkbox("コンパクト表示（余白を減らす）", value=True)
if dense:
    st.markdown(
        """
        <style>
        div[data-testid="stVerticalBlock"] { gap: 0.35rem; }
        div[data-testid="stMarkdown"] p { margin-bottom: 0.2rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

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

# 並べ替え
view = view.sort_values("推奨される在庫補充数量", ascending=False).reset_index(drop=True)

# 楽天URL列を事前に作る（ここは軽い）
view["rakuten_url"] = view.apply(lambda r: choose_page_url_from_row(r, url_colname=url_colname) or "", axis=1)

st.write(f"表示件数: **{len(view)}**（上位から {int(show_rows)} 件を表示）")

# ---- 即表示：一覧（画像なし）----
base_cols = []
if "Merchant SKU" in view.columns:
    base_cols.append("Merchant SKU")
base_cols += ["ASIN", "推奨される在庫補充数量", "rakuten_url"]
if "商品名" in view.columns:
    base_cols.insert(0, "商品名")

st.dataframe(view.head(int(show_rows))[base_cols], use_container_width=True, height=420)

st.divider()

# ---- 画像取得（あとから）----
st.subheader("🖼️ 画像表示（時間がかかってもOK）")

img_left, img_right = st.columns([1.4, 2.6], gap="large")

with img_left:
    img_top_n = st.number_input("画像を取得する上位件数（多いほど遅い）", min_value=10, max_value=int(show_rows), value=min(50, int(show_rows)), step=10)
    img_width = st.slider("画像サイズ（小さくすると1画面に増える）", min_value=30, max_value=120, value=45, step=5)
    start = st.button("画像取得を開始（上位N件）", type="primary")

with img_right:
    st.caption("まず上の表で全体を素早く確認 → 必要な上位だけ画像を取得する運用が速いです。")

# セッションに画像キャッシュ（URL→img_url）
if "img_cache" not in st.session_state:
    st.session_state["img_cache"] = {}

if start:
    target = view.head(int(img_top_n)).copy()

    # 画像表示用のコンパクトリスト
    for idx, row in target.iterrows():
        sku = normalize_text(row.get("Merchant SKU", ""))
        asin = normalize_text(row.get("ASIN", ""))
        qty = int(row.get("推奨される在庫補充数量", 0))
        name = normalize_text(row.get("商品名", ""))

        page_url = normalize_text(row.get("rakuten_url", ""))

        # 取得済みなら再取得しない
        if page_url in st.session_state["img_cache"]:
            img_url = st.session_state["img_cache"][page_url]
            status = "cache"
        else:
            res = fetch_rakuten_image_by_url(page_url)
            img_url = res.get("img_url")
            status = res.get("status")
            st.session_state["img_cache"][page_url] = img_url  # Noneでも保持（無限リトライ防止）

        # 1行表示（超コンパクト）
        c1, c2, c3, c4 = st.columns([0.4, 2.6, 0.9, 1.1], gap="small")
        with c1:
            if img_url:
                st.image(img_url, width=int(img_width))
            else:
                st.caption("—")

        with c2:
            # 情報は詰める（リンクも貼れるが長いので控えめに）
            t = []
            if name:
                t.append(f"{name}")
            if sku:
                t.append(f"SKU:{sku}")
            t.append(f"ASIN:{asin}")
            st.markdown("<br>".join(t), unsafe_allow_html=True)

        with c3:
            st.markdown(
                f"""
                <div style="
                    border-radius: 10px;
                    padding: 6px 8px;
                    border: 1px solid rgba(255,0,0,0.22);
                    background: rgba(255,0,0,0.06);
                    text-align: center;
                ">
                    <div style="font-size: 11px; opacity: 0.75;">発注推奨</div>
                    <div style="font-size: 22px; font-weight: 900; color: #d40000; line-height: 1.05;">
                        {qty}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with c4:
            # 取得元URLは短く（必要ならクリックできる）
            if page_url:
                st.link_button("楽天ページ", page_url, use_container_width=True)
            st.caption(status)

        st.divider()
