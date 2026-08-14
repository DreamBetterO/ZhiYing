from __future__ import annotations

import re
from pathlib import Path


SENSITIVE = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.I)


def decode(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def main() -> None:
    path = Path("QwenAPI.txt")
    if not path.exists():
        raise SystemExit("QwenAPI.txt not found")
    for raw_line in decode(path).splitlines():
        line = raw_line.strip()
        if not line:
            print()
            continue
        if SENSITIVE.search(line):
            if "=" in line:
                print(f"{line.split('=', 1)[0].strip()}=<REDACTED>")
            elif ":" in line:
                print(f"{line.split(':', 1)[0].strip()}: <REDACTED>")
            else:
                print("<REDACTED SENSITIVE LINE>")
            continue
        # Redact long bearer-like values even if the field has an unusual name.
        line = re.sub(r"\b(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{40,})\b", "<REDACTED>", line)
        print(line)


if __name__ == "__main__":
    main()
