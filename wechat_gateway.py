#!/usr/bin/env python3
"""微信消息到英语学习系统的轻量网关。"""
import sys
import io
import logging
from contextlib import redirect_stdout, redirect_stderr

from wechat_handler import handle_wechat_message


def main() -> int:
    if len(sys.argv) < 2:
        print("请输入消息")
        return 1

    message = " ".join(sys.argv[1:]).strip()
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    previous_disable = logging.root.manager.disable

    try:
        # Only return the user-facing handler result. Internal logs and prints
        # make the bridge agent unstable on long article generations.
        logging.disable(logging.CRITICAL)
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            result = handle_wechat_message(message)
    finally:
        logging.disable(previous_disable)

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
