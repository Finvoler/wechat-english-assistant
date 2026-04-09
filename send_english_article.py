#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
姣忔棩鑻辫鏂囩珷鐢熸垚鍜屽彂閫佽剼鏈?閫氳繃OpenClaw寰俊鎻掍欢鍙戦€佹秷鎭?"""

import subprocess
import json
import os
import sys
import tempfile
from pathlib import Path

def run_python_script():
    """鎵цPython鑴氭湰鐢熸垚鑻辫鏂囩珷"""
    python_exe = r"sys.executable"
    script_path = r"str(Path(__file__).parent / "generate_daily_article.py")"
    
    try:
        print(f"鎵ц鑴氭湰: {script_path}")
        
        # 鎵цPython鑴氭湰骞舵崟鑾疯緭鍑?        result = subprocess.run(
            [python_exe, script_path],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=120  # 2鍒嗛挓瓒呮椂
        )
        
        # 妫€鏌ヨ繑鍥炵爜
        if result.returncode == 0:
            output = result.stdout
            print(f"鑴氭湰鎵ц鎴愬姛锛岃緭鍑洪暱搴? {len(output)} 瀛楃")
            
            # 妫€鏌ユ槸鍚﹀寘鍚€愭瘡鏃ヨ嫳璇枃绔犮€?            if "銆愭瘡鏃ヨ嫳璇枃绔犮€? in output:
                print("鉁?鎴愬姛鐢熸垚浜嗚嫳璇枃绔?)
                return output
            else:
                print("鈿?璀﹀憡锛氳緭鍑轰腑鏈壘鍒般€愭瘡鏃ヨ嫳璇枃绔犮€戞爣璁?)
                return output
        else:
            print(f"鉁?鑴氭湰鎵ц澶辫触锛岃繑鍥炵爜: {result.returncode}")
            print(f"鏍囧噯閿欒: {result.stderr[:500]}")
            return None
            
    except subprocess.TimeoutExpired:
        print("鉁?鑴氭湰鎵ц瓒呮椂锛堣秴杩?鍒嗛挓锛?)
        return None
    except Exception as e:
        print(f"鉁?鎵ц鑴氭湰鏃跺嚭閿? {e}")
        return None

def create_wechat_message(article_content):
    """鍒涘缓寰俊娑堟伅"""
    # 纭繚鍐呭涓嶄负绌?    if not article_content:
        return "銆愭瘡鏃ヨ嫳璇涔犳彁閱掋€慭n\n浠婃棩鑻辫鏂囩珷鐢熸垚澶辫触锛岃鎵嬪姩妫€鏌ャ€?
    
    # 濡傛灉鍐呭澶暱锛屾埅鍙栧墠4000瀛楃锛堝井淇℃秷鎭暱搴﹂檺鍒讹級
    if len(article_content) > 4000:
        article_content = article_content[:4000] + "\n\n銆愬唴瀹硅繃闀匡紝宸叉埅鏂€?
    
    return article_content

def main():
    print("=" * 50)
    print("姣忔棩鑻辫鏂囩珷鐢熸垚鍜屽彂閫佷换鍔?)
    print("=" * 50)
    
    # 鎵цPython鑴氭湰鐢熸垚鏂囩珷
    article_content = run_python_script()
    
    if article_content:
        # 鍒涘缓寰俊娑堟伅
        wechat_message = create_wechat_message(article_content)
        
        # 鍒涘缓涓存椂鏂囦欢淇濆瓨娑堟伅鍐呭
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.txt', delete=False) as f:
            f.write(wechat_message)
            temp_file = f.name
        
        print(f"娑堟伅宸蹭繚瀛樺埌涓存椂鏂囦欢: {temp_file}")
        print("=" * 50)
        print("浠诲姟瀹屾垚锛?)
        print("=" * 50)
        
        # 杩斿洖涓存椂鏂囦欢璺緞锛屼緵OpenClaw Cron浣跨敤
        print(f"TEMP_FILE:{temp_file}")
    else:
        print("鉁?浠诲姟澶辫触锛氭湭鑳界敓鎴愯嫳璇枃绔?)
        sys.exit(1)

if __name__ == "__main__":
    main()
