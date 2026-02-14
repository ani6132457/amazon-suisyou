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

# --------- 超コンパクトCSS ---------
st.markdown("""
<style>
.block-container {padding-top:0.4rem; padding-bottom:0.4rem;}
div[data-testid="stVerticalBlock"] {gap:0.15rem;}
div[data-testid="stMarkdown"] p {margin:0;}
hr {margin:0.25rem 0;}
.small {font-size:11px; color:#666;}
.product-name {
    font-size:14px;
    font-weight:600;
    margin-bottom:8px;
}
</style>
""", unsafe_allow_html=True)

# ---------------- CSV ----------------
def read_inventory_csv(uploaded_file):
    try:
        return pd.read_csv(uploaded_file, encoding="cp932")
    except Exception:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding="utf-8")

def normalize(x):
    if pd.isna(x):
        return ""
    return str(x).strip()

def extract_7digits(sku):
    """
    SKU から数字だけを取り出し、その先頭 7 桁を返す。
    例:
      "ama-798_7560X11Y14" -> "79875601114" -> "7987560"
    """
    if not sku:
        return None
    digits = "".join(re.findall(r"\d+", str(sku)))
    return digits[:7] if len(digits) >= 7 else None

def extract_color(name):
    if not name:
        return ""
    m = re.search(r"[（(](.*?)[）)]", name)
    return m.group(1) if m else ""

# ---------------- 永続キャッシュ ----------------
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            dfc = pd.read_csv(CACHE_FILE)
            if "rakuten_url" in dfc.columns and "image_url" in dfc.columns:
                return dfc
        except Exception:
            pass
    return pd.DataFrame(columns=["rakuten_url", "image_url"])

def save_cache(df):
    df.to_csv(CACHE_FILE, index=False)

# ---------------- Selenium（メモリ対策：キャッシュしない / 1実行で1回だけ起動） ----------------
def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument("--lang=ja-JP")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--disable-sync")
    opts.add_argument("--metrics-recording-only")
    opts.add_argument("--no-first-run")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(25)
    return driver

def extract_img(html, base_url):
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

def fetch_image_with_driver(driver, url):
    if not url:
        return None
    try:
        driver.get(url)
        html = driver.page_source
        # ブロックページ簡易判定
        if "Reference #" in html or "Access Denied" in html:
            return None
        return extract_img(html, driver.current_url)
    except Exception:
        return None

# ---------------- UI ----------------
st.title("📦 発注推奨順")

uploaded = st.file_uploader("CSVをアップロード", type=["csv"])
if not uploaded:
    st.stop()

df = read_inventory_csv(uploaded)

# 必須列
if "ASIN" not in df.columns or "推奨される在庫補充数量" not in df.columns:
    st.error("CSVに必要な列（ASIN / 推奨される在庫補充数量）が見つかりません。")
    st.stop()

df["推奨される在庫補充数量"] = pd.to_numeric(
    df["推奨される在庫補充数量"], errors="coerce"
).fillna(0).astype(int)

df["ASIN"] = df["ASIN"].map(normalize)

if "Merchant SKU" in df.columns:
    df["Merchant SKU"] = df["Merchant SKU"].map(normalize)
else:
    df["Merchant SKU"] = ""

if "商品名" in df.columns:
    df["商品名"] = df["商品名"].map(normalize)
else:
    df["商品名"] = ""

COL_AVAILABLE = "販売可能な商品の合計"
COL_BACKORDER = "入荷待ち"

if COL_AVAILABLE not in df.columns:
    df[COL_AVAILABLE] = 0
if COL_BACKORDER not in df.columns:
    df[COL_BACKORDER] = 0

# 並べ替え（発注推奨の多い順）
df = df.sort_values("推奨される在庫補充数量", ascending=False).reset_index(drop=True)

# -------- 検索 --------
search = st.text_input("🔎 SKU / ASIN / 商品名 検索（部分一致）")
if search:
    s = search.lower()
    df = df[
        df["ASIN"].str.lower().str.contains(s, na=False)
        | df["Merchant SKU"].str.lower().str.contains(s, na=False)
        | df["商品名"].str.lower().str.contains(s, na=False)
    ]

# -------- 在庫切れのみ --------
only_soldout = st.checkbox("在庫切れのみ表示")
if only_soldout:
    df = df[pd.to_numeric(df[COL_AVAILABLE], errors="coerce").fillna(0) == 0]

# 楽天URL生成
df["rakuten_url"] = df["Merchant SKU"].apply(
    lambda x: RAKUTEN_ITEM.format(extract_7digits(x)) if extract_7digits(x) else ""
)

# キャッシュ
cache_df = load_cache()
cache_dict = dict(zip(cache_df["rakuten_url"], cache_df["image_url"]))

# 表示設定
left, right = st.columns([1.1, 1.9], gap="large")
with left:
    max_rows = st.number_input("表示件数", 50, 2000, 300, 50)
    img_size = st.slider("画像サイズ", 25, 70, 35)
with right:
    # ★ここがメモリ/負荷対策の肝：自動取得は上位N件だけ（ボタン無し）
    auto_fetch_top_n = st.number_input("画像を自動取得する上位件数", 0, 500, 60, 10)
    st.caption("キャッシュ済みは即表示。未取得は上位N件だけ順に取得します。")

rows = df.head(int(max_rows)).copy()

# 上位N件のうち、未キャッシュURLだけ取得対象にする
need_fetch = set()
if int(auto_fetch_top_n) > 0:
    for u in rows.head(int(auto_fetch_top_n))["rakuten_url"].tolist():
        if u and not isinstance(cache_dict.get(u, ""), str) or cache_dict.get(u, "") == "":
            need_fetch.add(u)

# 先に枠だけ全部作る（文字は即表示）
containers = []
for _, row in rows.iterrows():
    c = st.container()
    containers.append((c, row))

driver = None
try:
    if need_fetch:
        driver = make_driver()

    for c, row in containers:
        sku = row["Merchant SKU"]
        asin = row["ASIN"]
        name = row["商品名"]
        color = extract_color(name)
        qty = int(row["推奨される在庫補充数量"])
        url = row["rakuten_url"]

        # 在庫数
        available_raw = row[COL_AVAILABLE]
        backorder_raw = row[COL_BACKORDER]
        available = int(pd.to_numeric(available_raw, errors="coerce").fillna(0))
        backorder = int(pd.to_numeric(backorder_raw, errors="coerce").fillna(0))

        # 画像URL（キャッシュ優先）
        img_url = cache_dict.get(url, "") if url else ""

        # 未キャッシュ & 取得対象ならSeleniumで取得して保存
        if (not img_url) and url and (url in need_fetch) and driver is not None:
            new_img = fetch_image_with_driver(driver, url)
            if new_img:
                img_url = new_img
                cache_dict[url] = new_img
                cache_df.loc[len(cache_df)] = [url, new_img]
                save_cache(cache_df)
            else:
                # 失敗も記録（無限に取りに行かない）
                cache_dict[url] = ""
                cache_df.loc[len(cache_df)] = [url, ""]
                save_cache(cache_df)

        with c:
            col1, col2, col3 = st.columns([0.32, 4, 0.8])

            # ---- 画像（正方形）----
            with col1:
                if img_url:
                    st.markdown(
                        f"""
                        <div style="width:{img_size}px;height:{img_size}px;
                                    display:flex;align-items:center;justify-content:center;
                                    overflow:hidden;border-radius:4px;">
                            <img src="{img_url}"
                                 style="max-width:100%;max-height:100%;object-fit:contain;">
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                else:
                    # 画像無しでも高さが揃うように枠を出す
                    st.markdown(
                        f"""
                        <div style="width:{img_size}px;height:{img_size}px;
                                    border-radius:4px;background:rgba(0,0,0,0.04);">
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

            # ---- 商品情報 ----
            with col2:
                st.markdown(f"<div class='product-name'>{name}</div>", unsafe_allow_html=True)

                line = f"SKU:{sku} | ASIN:{asin}"
                if color:
                    line += f" | <b>{color}</b>"
                st.markdown(line, unsafe_allow_html=True)

                if available == 0:
                    st.markdown(
                        f"""
                        <div style="font-size:15px;font-weight:600;margin-top:12px;line-height:1.2;">
                            販売可能: <span style="color:#007bff;">{available}</span>
                            ｜ 入荷待ち: <span style="color:#ff6600;">{backorder}</span>
                            <span style="
                                margin-left:8px;
                                padding:2px 6px;
                                font-size:12px;
                                font-weight:700;
                                background:#d40000;
                                color:white;
                                border-radius:4px;">在庫切れ</span>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        f"""
                        <div style="font-size:15px;font-weight:600;margin-top:12px;line-height:1.2;">
                            販売可能: <span style="color:#007bff;">{available}</span>
                            ｜ 入荷待ち: <span style="color:#ff6600;">{backorder}</span>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

            # ---- 発注 ----
            with col3:
                st.markdown(
                    f"""
                    <div style="padding:4px;text-align:center;
                                background:rgba(255,0,0,0.12);border-radius:6px;">
                        <div style="font-size:9px;">発注</div>
                        <div style="font-size:17px;font-weight:900;color:#d40000;">
                            {qty}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.markdown("<hr>", unsafe_allow_html=True)

finally:
    # ★必ず終了してメモリを解放
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass
