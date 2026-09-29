# Vercel Python 函数入口：打包根目录 app.py 的 Flask 实例。
# vercel.json 将所有路径 rewrite 到 /api/index?__path=<原始路径>；
# 平台新行为下函数内 PATH_INFO 会变成目标路径，这里按 __path 还原，
# 并保留原始查询串（如 ?fk=75），使本地/线上路由行为一致。
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app as flask_app  # noqa: E402


def app(environ, start_response):
    qs = environ.get("QUERY_STRING", "") or ""
    if "__path=" in qs:
        path = "/"
        rest = []
        for k, v in parse_qsl(qs, keep_blank_values=True):
            if k == "__path":
                path = "/" + v.lstrip("/") if v else "/"
            else:
                rest.append((k, v))
        environ["PATH_INFO"] = path
        environ["QUERY_STRING"] = urlencode(rest)
        environ["SCRIPT_NAME"] = ""
    return flask_app(environ, start_response)
