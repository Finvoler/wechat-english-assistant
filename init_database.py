#!/usr/bin/env python3
"""初始化数据库并创建表。"""

from database import engine
from models import Base


def main():
    Base.metadata.create_all(bind=engine)
    print("数据库初始化成功！")
    print("已创建表: vocabulary, articles")


if __name__ == "__main__":
    main()
