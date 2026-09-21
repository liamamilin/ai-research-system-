#!/usr/bin/env python3
"""
日志清理脚本
清理旧的日志文件，保留最近的日志文件
"""

import os
import glob
from datetime import datetime, timedelta


def cleanup_logs(logs_dir: str = "logs", days: int = 7):
    """
    清理旧的日志文件
    
    Args:
        logs_dir: 日志目录
        days: 保留天数
    """
    if not os.path.exists(logs_dir):
        print(f"日志目录不存在: {logs_dir}")
        return
    
    # 获取当前时间
    now = datetime.now()
    cutoff_time = now - timedelta(days=days)
    
    # 查找所有日志文件
    log_files = glob.glob(os.path.join(logs_dir, "*.log*"))
    
    deleted_count = 0
    total_size = 0
    
    for log_file in log_files:
        # 获取文件修改时间
        mtime = datetime.fromtimestamp(os.path.getmtime(log_file))
        
        # 如果文件超过保留天数，删除
        if mtime < cutoff_time:
            file_size = os.path.getsize(log_file)
            os.remove(log_file)
            deleted_count += 1
            total_size += file_size
            print(f"已删除: {log_file} ({file_size / 1024:.2f} KB)")
    
    print(f"\n清理完成！")
    print(f"删除文件数: {deleted_count}")
    print(f"释放空间: {total_size / 1024:.2f} KB")
    print(f"保留天数: {days} 天")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="清理旧的日志文件")
    parser.add_argument("--dir", default="logs", help="日志目录（默认：logs）")
    parser.add_argument("--days", type=int, default=7, help="保留天数（默认：7）")
    
    args = parser.parse_args()
    
    cleanup_logs(args.dir, args.days)