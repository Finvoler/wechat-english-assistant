#!/usr/bin/env python3
"""微信消息到英语学习系统的轻量网关。"""
import sys
import io
from wechat_handler import handle_wechat_message


def main() -> int:
    if len(sys.argv) < 2:
        print("请输入消息")
        return 1

    message = " ".join(sys.argv[1:]).strip()
    result = handle_wechat_message(message)

    if result is None:
        # 使用sys.stdout.buffer.write来避免编码问题
        sys.stdout.buffer.write("未触发英语学习模块。发送“英语”进入模块。".encode('utf-8'))
        sys.stdout.buffer.write(b'\n')
    else:
        # 使用sys.stdout.buffer.write来避免编码问题
        sys.stdout.buffer.write(result.encode('utf-8'))
        sys.stdout.buffer.write(b'\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
