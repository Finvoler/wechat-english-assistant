#!/usr/bin/env python3
"""从本地 txt 批量导入单词。
格式：
1) word
2) word|definition
3) word|definition|example
"""
from pathlib import Path

from word_manager import add_word

project_root = Path(__file__).parent
DEFAULT_FILE = project_root / "vocabulary_input.txt"


def import_words(file_path: Path) -> tuple[int, int]:
    added = 0
    skipped = 0

    if not file_path.exists():
        return added, skipped

    lines = file_path.read_text(encoding="utf-8").splitlines()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split("|")]
        word = parts[0]
        definition = parts[1] if len(parts) > 1 else ""
        example = parts[2] if len(parts) > 2 else ""

        ok = add_word(word, definition, example, added_via="file-sync", verbose=False)
        if ok:
            added += 1
        else:
            skipped += 1

    return added, skipped


def main() -> None:
    file_path = DEFAULT_FILE
    added, skipped = import_words(file_path)
    print(f"导入完成: 新增 {added}，跳过 {skipped}")


if __name__ == "__main__":
    main()
