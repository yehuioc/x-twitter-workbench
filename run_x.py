"""X 工作台：一个服务，UI / HTTP / MCP 共用。"""
import argparse
import os
os.environ.setdefault('PYTHONUTF8', '1')
from x_workbench.server import serve

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'mcp'], nargs='?', default='serve')
    parser.add_argument('--port', type=int, default=8915)
    args = parser.parse_args()
    if args.command == 'mcp':
        from x_workbench.mcp import main
        main(args.port)
    else:
        serve(args.port)
