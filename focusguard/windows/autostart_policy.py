"""固定、离线的备考自启期限；不跟随电脑的本地时区改变。"""
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), "北京时间")
POLICY_ID = "exam-2027"
AUTOSTART_UNTIL = datetime(2026, 12, 21, tzinfo=BEIJING)
DEADLINE_TEXT = "2026 年 12 月 21 日 00:00（北京时间）"
EXAM_SOURCE = "http://www.moe.gov.cn/srcsite/A15/moe_778/s3261/202609/t20260923_1451734.html"


def before_deadline(now=None):
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("自启期限检查需要带时区的时间。")
    return now < AUTOSTART_UNTIL


def new_policy(now=None):
    return {"id": POLICY_ID, "until": AUTOSTART_UNTIL.isoformat(),
            "expired": not before_deadline(now)}


def validate_policy(value):
    if value is None:  # 旧版安装不在后台静默加入新的限期策略。
        return None
    if (not isinstance(value, dict) or value.get("id") != POLICY_ID
            or value.get("until") != AUTOSTART_UNTIL.isoformat()
            or type(value.get("expired")) is not bool):
        raise ValueError("自启期限记录不兼容；不会自动修改启动项，请检查安装记录。")
    return value
