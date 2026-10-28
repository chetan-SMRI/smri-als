import re
from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_device_datetime(value: str, tz) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(value, fmt).replace(tzinfo=tz)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def parse_key_values(value: str) -> dict[str, str]:
    """Parse ADMS key/value records while preserving values containing spaces."""
    matches = list(re.finditer(r"(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=", value))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        result[match.group(1).upper()] = value[start:end].strip()
    return result


def parse_attlog_line(line: str, tz) -> dict | None:
    fields = line.rstrip("\r").split("\t")
    if len(fields) < 2:
        fields = re.split(r"\s+", line.strip(), maxsplit=6)
    if len(fields) < 2:
        return None
    punch_time = parse_device_datetime(fields[1], tz)
    if punch_time is None:
        return None
    return {
        "employee_code": fields[0].strip(),
        "punch_time": punch_time,
        "status": fields[2].strip() if len(fields) > 2 else "0",
        "verify": fields[3].strip() if len(fields) > 3 else "0",
        "work_code": fields[4].strip() if len(fields) > 4 else "",
        "reserved": fields[5].strip() if len(fields) > 5 else None,
        "raw_data": line,
    }


def parse_user_line(line: str) -> dict | None:
    values = parse_key_values(line)
    code = values.get("PIN") or values.get("UID")
    if not code:
        fields = line.rstrip("\r").split("\t")
        if not fields or not fields[0].strip():
            return None
        code = fields[0].strip()
        return {
            "employee_code": code,
            "name": fields[1].strip() if len(fields) > 1 else None,
            "privilege": fields[2].strip() if len(fields) > 2 else None,
            "password": fields[3].strip() if len(fields) > 3 else None,
            "card_number": fields[4].strip() if len(fields) > 4 else None,
            "raw_data": line,
        }
    return {
        "employee_code": code,
        "name": values.get("NAME"),
        "privilege": values.get("PRI") or values.get("PRIVILEGE"),
        "password": values.get("PASSWD") or values.get("PASSWORD"),
        "card_number": values.get("CARD"),
        "raw_data": line,
    }


def option_response(serial: str, stamp: str, op_stamp: str, error_delay: int, interval: int) -> str:
    return "\n".join(
        [
            f"GET OPTION FROM: {serial}",
            f"Stamp={stamp or '0'}",
            f"OpStamp={op_stamp or '0'}",
            "PhotoStamp=None",
            f"ErrorDelay={error_delay}",
            "Delay=1",
            f"TransInterval={interval}",
            "TransFlag=1111000000",
            "Realtime=1",
            "Encrypt=0",
        ]
    )

