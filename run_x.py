"""X 工作台：一个服务，UI / HTTP / MCP 共用。"""
import argparse
import os
os.environ.setdefault('PYTHONUTF8', '1')
from x_workbench.server import serve

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'mcp', 'import-bookmarks'], nargs='?', default='serve')
    parser.add_argument('--port', type=int, default=8915)
    parser.add_argument('--posts-dir',help='personal-ingestion 的 x/posts 目录；只读原库')
    parser.add_argument('--attachments-dir',help='旧库附件根目录；只复制已登记的图片')
    args = parser.parse_args()
    if args.command == 'import-bookmarks':
        if not args.posts_dir: parser.error('import-bookmarks 需要 --posts-dir')
        from x_workbench.store import Store, dumps
        from x_workbench.bookmarks import import_archive
        print(dumps(import_archive(Store(recover=False),args.posts_dir,args.attachments_dir)))
    elif args.command == 'mcp':
        from x_workbench.mcp import main
        main(args.port)
    else:
        serve(args.port)
