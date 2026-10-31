"""本地假小说站：用于端到端验证 novel-dl 命令。"""
from http.server import BaseHTTPRequestHandler, HTTPServer

TOC = ('<html><head><title>测试小说_在线阅读</title></head><body>' +
       ''.join(f'<a href="/book/1/{i}.html">第{i}章 夜行{i}</a>' for i in range(1, 7)) +
       '</body></html>')


def chapter(n):
    body = ('<html><head><title>第{n}章 夜行{n}</title></head><body><h1>第{n}章 夜行{n}</h1>'
            '<div id="content">' + '夜色沉沉，他提灯走过长街。' * 40 + '</div></body></html>').format(n=n)
    if n == 3:  # 坏章节：正文过短，按失败跳过
        body = '<html><body><div id="content">短</div></body></html>'
    return body


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/book/1/':
            body = TOC.encode()
        else:
            n = self.path.rstrip('.html').split('/')[-1]
            body = chapter(int(n)).encode() if n.isdigit() else b'404'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音
        pass

HTTPServer(('127.0.0.1', 8765), H).serve_forever()
