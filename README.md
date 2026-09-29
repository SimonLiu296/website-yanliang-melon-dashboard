# 阎良甜瓜数据看板（Flask + ECharts + Neon + Vercel）

`yanliang-melon-dashboard` 的部署版：数据存 Neon Postgres，可一键部署到 Vercel。
原文件夹保持只读，本文件夹自包含（数据快照、模板、静态资源齐全）。

## 功能页面

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/` | 产业概览 | 关键指标卡片、平台分布饼图、价格分布直方图 |
| `/platform` | 平台对比 | 京东/淘宝平均单价、SKU 数、评分柱状图 + 明细表 |
| `/spec` | 规格价格 | 净重 vs 单价折线图 + 规格明细表 |
| `/variety` | 品种分析 | 品种平均单价柱状图 + 品种口碑表 |
| `/sentiment` | 评论情感 | 四维度得分、词频 Top20、可搜索评论列表 |
| `/export` | 数据导出 | 全部 CSV 列表与下载 |

API：`/api/overview`、`/api/platform`、`/api/spec`、`/api/variety`、`/api/sentiment`、`/api/tables`
CSV 下载：`/export/csv/<table_name>`（带 UTF-8 BOM）

## 目录结构

```
website-yanliang-melon-dashboard/
├── app.py              # Flask 应用（读 Neon：pd.read_sql + 进程内缓存；Vercel 框架入口）
├── vercel.json         # 项目配置（函数区域在 Dashboard → Settings → Functions 设置）
├── requirements.txt    # flask/pandas/sqlalchemy/psycopg[binary]/python-dotenv
├── seed.py             # CSV 快照 → Neon 灌库 + 行数对账 + meta 表
├── .env                # 真实 DATABASE_URL（已 gitignore，不提交）
├── .env.example        # 连接串模板
├── .gitignore          # .env / .vercel / __pycache__
├── data/               # 数据快照（9 张 CSV，seed.py 从原文件夹复制）
├── templates/          # 页面模板（与原版一致）
└── public/static/      # css/js/favicon（本地 Flask 与 Vercel CDN 同一路径）
```

## 本地运行

```bash
cd website-yanliang-melon-dashboard
pip install -r requirements.txt          # 已装可跳过
python seed.py                           # 首次：灌库（读 .env 的 DATABASE_URL）
python app.py                            # -> http://127.0.0.1:5000
```

端口被占时自动顺延并打印新地址；可用 `PORT=8000 python app.py` 指定。

## 数据说明（Neon）

- 9 张逻辑表以**同名表**存于 Neon，列名保留中文表头；数值列建为数值类型，行为与 CSV 一致。
- `meta` 表记录每张表的 `data_date / source_file / rows / size_kb / loaded_at`，
  `/export` 页的数据日期与行数来自 `meta`。
- 数据更新：重新执行 `python seed.py`（自动按「表名_日期最新」从原文件夹取源文件并刷新快照）。
- 连接串在 `.env`；代码会把 `channel_binding=require` 自动降级为 `disable`
  （部分 libpq/驱动不支持），`sslmode=require` 保持不变。

## Vercel 部署（手动执行）

```bash
# 一次性准备
npm i -g vercel                          # 或 npx vercel
cd website-yanliang-melon-dashboard
vercel login

# 首次部署（预览）
vercel
# 生产部署
vercel --prod

# 绑定数据库（每个环境都要设一次：production / preview）
vercel env add DATABASE_URL
# 提示 Paste 时，粘贴 .env 中 DATABASE_URL= 后面的整行连接串（不含 DATABASE_URL=）
```

- Vercel 按 Flask **框架预设**直接托管根目录 `app.py`（原生路径路由，无需 rewrite）；
  `public/**` 由 CDN 直出。不要在 `vercel.json` 里配 rewrite——内部重写会把
  PATH_INFO 覆盖成目标路径，导致 404 JSON（历史故障根因）。
- 函数区域（新加坡 `sin1`，与 Neon 同区）：Dashboard → Settings → Functions →
  Serverless Region 选 `Singapore (sin1)`。
- GitHub 导入备选：推送本文件夹到仓库子目录 → Vercel Import 时 Root Directory 填
  `website-yanliang-melon-dashboard`；`.env` 不会随仓库上传（已 gitignore），
  记得在 Vercel 项目 Settings → Environment Variables 里设置 `DATABASE_URL`。

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 500 + `缺少 DATABASE_URL` | 本地缺 `.env`，或 Vercel 未设置 `DATABASE_URL` |
| 页面 500 / API `internal error` | 连接串错误或 Neon 暂不可达；本地跑 `python seed.py` 可快速验证 |
| CSV 导出行数为 0 | 表未灌库，执行 `python seed.py` |
| 导出 Excel 乱码 | 已带 UTF-8 BOM，不应出现；检查是否绕过 `/export/csv` 直接拿接口数据 |
