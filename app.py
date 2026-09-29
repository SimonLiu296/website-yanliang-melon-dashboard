# -*- coding: utf-8 -*-
"""阎良甜瓜数据看板（Flask + ECharts，数据存储于 Neon Postgres）

本地运行：python app.py  ->  http://127.0.0.1:5000
Vercel：api/index.py 导出 app；静态资源位于 public/static/（CDN 直出）。

数据：9 张逻辑表在 Neon 中与表名同名（列名保留中文表头），
      灌库：python seed.py（源 CSV 快照在 data/ 下）；数据日期见 meta 表。
"""
import io
import json
import os
import re
import socket
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from flask import (Flask, abort, jsonify, render_template, request,
                   send_file, send_from_directory)
from sqlalchemy import create_engine

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__, static_folder="public/static", static_url_path="/static")

# 逻辑表名 -> 数据字典（实际数据在 Neon，同名表）
TABLE_FILES = {
    "products_clean": "products_clean",
    "skus_clean": "skus_clean",
    "prices_clean": "prices_clean",
    "reviews_sentiment": "reviews_sentiment",
    "platform_comparison": "platform_comparison",
    "spec_price": "spec_price",
    "variety_price": "variety_price",
    "sentiment_summary": "sentiment_summary",
    "keyword_freq": "keyword_freq",
}

TABLE_LABELS = {
    "products_clean": "商品主表",
    "skus_clean": "SKU 明细",
    "prices_clean": "价格记录",
    "reviews_sentiment": "评论情感明细",
    "platform_comparison": "平台对比",
    "spec_price": "规格价格",
    "variety_price": "品种价格",
    "sentiment_summary": "情感汇总",
    "keyword_freq": "词频统计",
}

PLATFORM_NAMES = {"jd": "京东", "taobao": "淘宝", "tmall": "天猫"}

_cache = {}


def _db_url():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("缺少 DATABASE_URL（复制 .env.example 为 .env 并填写，或在 Vercel 设置环境变量）")
    # Neon 连接串带 channel_binding=require，部分驱动不支持 → 降级为 disable（保留 sslmode=require）
    # 显式指定 psycopg3 驱动（postgresql+psycopg://）
    url = re.sub(r"channel_binding=\w+", "channel_binding=disable", url)
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)


engine = create_engine(_db_url(), pool_pre_ping=True, pool_recycle=300,
                       connect_args={"connect_timeout": 10})


def _meta():
    """meta 表：table_name / data_date / source_file / rows / size_kb / loaded_at"""
    if "meta" not in _cache:
        _cache["meta"] = pd.read_sql('SELECT * FROM "meta"', engine)
    return _cache["meta"]


def read_table(name):
    key = "df:" + name
    if key in _cache:
        return _cache[key]
    df = pd.read_sql(f'SELECT * FROM "{name}"', engine)
    _cache[key] = df
    return df


def table(name):
    if name not in TABLE_FILES:
        abort(404)
    return read_table(name)


def data_date(name):
    m = _meta()
    row = m[m["table_name"].astype(str) == name]
    if len(row):
        return str(row["data_date"].iloc[0])
    return "-"


def meta_info(name):
    m = _meta()
    row = m[m["table_name"].astype(str) == name]
    if len(row):
        r = row.iloc[0]
        return {"source_file": str(r.get("source_file", "")),
                "rows": int(r.get("rows", 0)),
                "size_kb": float(r.get("size_kb", 0))}
    return {"source_file": "", "rows": 0, "size_kb": 0.0}


def to_records(df):
    """DataFrame -> JSON 友好的 list[dict]（NaN 转 null）。"""
    return json.loads(df.to_json(orient="records", force_ascii=False))


def safe_mean(series):
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 2)


def platform_label(code):
    return PLATFORM_NAMES.get(str(code), str(code))


def price_histogram(series, bins=10):
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {"bins": [], "counts": []}
    low, high = float(values.min()), float(values.max())
    if low == high:
        return {"bins": [round(low, 2)], "counts": [int(values.size)]}
    counts, edges = pd.cut(values, bins=bins, retbins=True, duplicates="drop")
    freq = counts.value_counts().sort_index()
    labels = [f"{edges[i]:.1f}~{edges[i + 1]:.1f}" for i in range(len(edges) - 1)]
    return {"bins": labels, "counts": [int(x) for x in freq.tolist()]}


def sentiment_by_variety():
    """按品种汇总评论情感（按评论数加权）。"""
    products = table("products_clean")[["商品FK", "品种"]]
    summary = table("sentiment_summary")
    merged = summary.merge(products, on="商品FK", how="left")
    merged["品种"] = merged["品种"].fillna("其他")
    merged["review_count"] = pd.to_numeric(merged["review_count"], errors="coerce").fillna(0)
    dims = ["sweet", "taste", "fresh", "logistics", "overall"]
    rows = []
    for variety, grp in merged.groupby("品种"):
        total = float(grp["review_count"].sum())
        item = {"品种": variety, "评论数": int(total)}
        for dim in dims:
            col = f"avg_{dim}_score"
            vals = pd.to_numeric(grp[col], errors="coerce").fillna(0)
            weight = grp["review_count"]
            item[col] = round(float((vals * weight).sum() / total), 4) if total else 0.0
        rows.append(item)
    rows.sort(key=lambda x: x["评论数"], reverse=True)
    return rows


# ---------------------------------------------------------------- 页面路由
@app.route("/")
def index():
    return render_template("index.html", active="index")


@app.route("/platform")
def platform():
    return render_template("platform.html", active="platform")


@app.route("/spec")
def spec():
    return render_template("spec.html", active="spec")


@app.route("/variety")
def variety():
    return render_template("variety.html", active="variety")


@app.route("/sentiment")
def sentiment():
    return render_template("sentiment.html", active="sentiment")


@app.route("/export")
def export_page():
    items = []
    for name in TABLE_FILES:
        info = meta_info(name)
        try:
            rows = len(read_table(name))
        except Exception:
            rows = info["rows"]
        items.append({
            "name": name,
            "label": TABLE_LABELS.get(name, name),
            "file": info["source_file"] or name,
            "date": data_date(name),
            "rows": rows,
            "size": f"{info['size_kb']:.1f} KB",
        })
    return render_template("export.html", active="export", tables=items)


# ---------------------------------------------------------------- API
@app.route("/api/overview")
def api_overview():
    products = table("products_clean")
    skus = table("skus_clean")
    prices = table("prices_clean")

    platform_counts = products["平台"].map(platform_label).value_counts()
    variety_counts = products["品种"].fillna("其他").value_counts()

    kpis = {
        "product_count": int(len(products)),
        "sku_count": int(len(skus)),
        "price_count": int(len(prices)),
        "review_count": int(len(table("reviews_sentiment"))),
        "avg_unit_price": safe_mean(skus["单价元/斤"]),
        "avg_product_price": safe_mean(products["最新价"]),
        "platform_count": int(products["平台"].nunique()),
        "variety_count": int(products["品种"].nunique()),
    }

    return jsonify({
        "kpis": kpis,
        "platform_pie": [
            {"name": k, "value": int(v)} for k, v in platform_counts.items()
        ],
        "price_histogram": price_histogram(products["最新价"]),
        "variety_bar": [
            {"name": k, "value": int(v)} for k, v in variety_counts.head(8).items()
        ],
    })


@app.route("/api/platform")
def api_platform():
    df = table("platform_comparison").copy()
    df["平台"] = df["平台"].map(platform_label)
    table_data = to_records(df)
    for row in table_data:
        for key in ("平均评分", "平均单价元/斤"):
            if row.get(key) is None:
                row[key] = None

    categories = df["平台"].tolist()

    def series(col):
        return [None if pd.isna(v) else float(v) for v in df[col]]

    return jsonify({
        "categories": categories,
        "avg_price": series("平均价格"),
        "median_price": series("中位价格"),
        "sku_count": [None if pd.isna(v) else int(v) for v in df["SKU数"]],
        "product_count": [None if pd.isna(v) else int(v) for v in df["商品数"]],
        "rating": series("平均评分"),
        "unit_price": series("平均单价元/斤"),
        "review_count": [None if pd.isna(v) else int(v) for v in df["评论总数"]],
        "table": table_data,
    })


@app.route("/api/spec")
def api_spec():
    df = table("spec_price").copy().sort_values("净重规格")
    return jsonify({
        "weight": [float(v) for v in df["净重规格"]],
        "avg": [None if pd.isna(v) else float(v) for v in df["平均单价元/斤"]],
        "median": [None if pd.isna(v) else float(v) for v in df["中位单价元/斤"]],
        "low": [None if pd.isna(v) else float(v) for v in df["最低单价元/斤"]],
        "high": [None if pd.isna(v) else float(v) for v in df["最高单价元/斤"]],
        "sku_count": [None if pd.isna(v) else int(v) for v in df["SKU数"]],
        "table": to_records(df),
    })


@app.route("/api/variety")
def api_variety():
    df = table("variety_price").copy().sort_values("平均单价元/斤", ascending=False)
    rep = {row["品种"]: row for row in sentiment_by_variety()}
    table_data = to_records(df)
    for row in table_data:
        extra = rep.get(row.get("品种"))
        if extra:
            row["评论数"] = extra["评论数"]
            row["甜度得分"] = round(extra["avg_sweet_score"] * 100, 1)
            row["口感得分"] = round(extra["avg_taste_score"] * 100, 1)
            row["整体好评率"] = round(extra["avg_overall_score"] * 100, 1)
        else:
            row.setdefault("评论数", 0)
            row.setdefault("甜度得分", None)
            row.setdefault("口感得分", None)
            row.setdefault("整体好评率", None)
    return jsonify({
        "categories": [str(v) for v in df["品种"]],
        "avg_price": [None if pd.isna(v) else float(v) for v in df["平均单价元/斤"]],
        "median_price": [None if pd.isna(v) else float(v) for v in df["中位单价元/斤"]],
        "sku_count": [None if pd.isna(v) else int(v) for v in df["SKU数"]],
        "rating": [None if pd.isna(v) else float(v) for v in df["平均评分"]],
        "table": table_data,
        "reputation": [row for row in table_data if row.get("评论数")],
    })


@app.route("/api/sentiment")
def api_sentiment():
    summary = table("sentiment_summary")
    weights = pd.to_numeric(summary["review_count"], errors="coerce").fillna(0)
    dims = {}
    for dim, label in (("sweet", "甜度"), ("taste", "口感"), ("fresh", "新鲜度"),
                       ("logistics", "物流"), ("overall", "整体")):
        col = f"avg_{dim}_score"
        vals = pd.to_numeric(summary[col], errors="coerce").fillna(0)
        dims[label] = round(float((vals * weights).sum() / weights.sum()), 4) if weights.sum() else 0.0

    keywords = table("keyword_freq").sort_values("freq", ascending=False).head(20)

    reviews = table("reviews_sentiment").copy()
    q = (request.args.get("q") or "").strip()
    if q:
        mask = (reviews["内容"].fillna("").astype(str).str.contains(q, regex=False)
                | reviews["用户"].fillna("").astype(str).str.contains(q, regex=False))
        reviews = reviews[mask]
    reviews = reviews.sort_values("评分", ascending=False, na_position="last")
    limit = min(int(request.args.get("limit", 50) or 50), 500)
    total = int(len(reviews))
    view = reviews.head(limit)[["商品FK", "用户", "内容", "评分", "sweet_score",
                                "taste_score", "fresh_score", "logistics_score",
                                "overall_score", "来源"]]
    review_rows = to_records(view)
    for row in review_rows:
        row["商品FK"] = int(row["商品FK"]) if row.get("商品FK") is not None else None

    return jsonify({
        "dimensions": {
            "categories": ["甜度", "口感", "新鲜度", "物流"],
            "values": [dims["甜度"], dims["口感"], dims["新鲜度"], dims["物流"]],
            "overall": dims["整体"],
        },
        "keywords": {
            "words": [str(w) for w in keywords["word"]],
            "freqs": [int(f) for f in keywords["freq"]],
        },
        "reviews": {"total": total, "items": review_rows},
        "summary_table": to_records(summary.head(200)),
    })


# ---------------------------------------------------------------- CSV 导出
@app.route("/export/csv/<table_name>")
def export_csv(table_name):
    if table_name not in TABLE_FILES:
        abort(404)
    try:
        df = read_table(table_name)
    except Exception:
        abort(404)
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)
    return send_file(buf, mimetype="text/csv",
                     as_attachment=True, download_name=f"{table_name}.csv")


@app.route("/api/tables")
def api_tables():
    return jsonify([{"name": k, "label": TABLE_LABELS.get(k, k),
                     "path": TABLE_FILES[k]} for k in TABLE_FILES])


@app.route("/favicon.ico")
def favicon():
    """浏览器默认请求 /favicon.ico，直接指向 public/static 下的图标，避免 404。"""
    return send_from_directory(str(BASE_DIR / "public" / "static"), "favicon.ico",
                               mimetype="image/x-icon")


@app.errorhandler(404)
def not_found(err):
    if request.path.startswith("/api/") or request.path.startswith("/export/csv/"):
        return jsonify({"error": "not found"}), 404
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(err):
    if request.path.startswith("/api/") or request.path.startswith("/export/csv/"):
        return jsonify({"error": "internal error"}), 500
    return ("服务暂时不可用，请稍后重试", 500)


def find_free_port(host, start_port, limit=20):
    """从 start_port 起探测可用端口（与 werkzeug 相同的 SO_REUSEADDR 语义）。"""
    for port in range(start_port, start_port + limit):
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(fam, socket.SOCK_STREAM)
        if fam == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port, 0) if fam == socket.AF_INET6 else (host, port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    return None


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    want = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")

    port = find_free_port(host, want)
    if port is None:
        raise SystemExit(f"[错误] {host} 起始端口 {want} 附近 20 个端口均不可用，请指定 PORT。")
    if port != want:
        print(f"[提示] 端口 {want} 不可用（可能被其它程序占用），已自动切换为 {port}")

    print(f" * 阎良甜瓜数据看板（Neon）: http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
