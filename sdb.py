from __future__ import print_function
try:
    import readline
except ImportError:
    print("Module readline not available.")
else:
    if 'libedit' in readline.__doc__:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")

import sys

import cmd
import contextlib
import errno
import logging
import os
import pprint
import re
import select
import socket
import sys
import threading
from cStringIO import StringIO
from multiprocessing import process
from pdb import Pdb
from Queue import Queue, Empty

from pygments import highlight
from pygments.lexers import PythonLexer
from pygments.formatters import Terminal256Formatter


__all__ = (
    'SDB_HOST', 'SDB_PORT', 'SDB_NOTIFY_HOST',
    'DEFAULT_PORT', 'Sdb', 'debugger', 'set_trace',
)

DEFAULT_PORT = 6899

SDB_HOST = os.environ.get('SDB_HOST') or '127.0.0.1'
SDB_PORT = int(os.environ.get('SDB_PORT') or DEFAULT_PORT)
SDB_NOTIFY_HOST = os.environ.get('SDB_NOTIFY_HOST') or '127.0.0.1'
SDB_CONTEXT_LINES = os.environ.get('SDB_CONTEXT_LINES') or 60

#: Holds the currently active debugger.
_current = [None]

_frame = getattr(sys, '_getframe')

NO_AVAILABLE_PORT = """\
Couldn't find an available port.

Please specify one using the SDB_PORT environment variable.
"""

BANNER = """\
{self.ident}: Ready to connect: telnet {self.host} {self.port}

Type `exit` in session to continue.

{self.ident}: Waiting for client...
"""

SESSION_STARTED = '{self.ident}: Now in session with {self.remote_addr}.'
SESSION_ENDED = '{self.ident}: Session with {self.remote_addr} ended.'


class Sdb(Pdb):
    """Socket-based debugger."""

    me = 'Socket Debugger'
    _prev_outs = None
    _sock = None

    def __init__(self, host=SDB_HOST, port=SDB_PORT,
                 notify_host=SDB_NOTIFY_HOST, context_lines=SDB_CONTEXT_LINES,
                 port_search_limit=100, port_skew=+0, out=sys.stdout):
        self.active = True
        self.out = out

        self._prev_handles = sys.stdin, sys.stdout

        self.notify_host = notify_host
        self.context_lines = int(context_lines)
        self._sock, this_port = self.get_avail_port(
            host, port, port_search_limit, port_skew,
        )
        self._sock.setblocking(1)
        self._sock.listen(1)
        self.host = host
        self.port = this_port
        self.ident = '{0}:{1}'.format(self.me, this_port)
        self.say(BANNER.format(self=self))

        self._client, address = self._sock.accept()
        self._client.setblocking(1)
        self.remote_addr = ':'.join(str(v) for v in address)
        self.say(SESSION_STARTED.format(self=self))
        self._handle = sys.stdin = sys.stdout = self._client.makefile('rw')
        Pdb.__init__(self, completekey='tab',
                     stdin=self._handle, stdout=self._handle)

    def get_avail_port(self, host, port, search_limit=100, skew=+0):
        try:
            _, skew = process._current_process.name.split('-')
            skew = int(skew)
        except ValueError:
            pass
        this_port = None
        for i in range(search_limit):
            _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            this_port = port + skew + i
            try:
                _sock.bind((host, this_port))
            except socket.error as exc:
                if exc.errno in [errno.EADDRINUSE, errno.EINVAL]:
                    continue
                raise
            else:
                if self.notify_host:
                    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(
                        str(this_port), (self.notify_host, 6899)
                    )
                return _sock, this_port
        else:
            import pdb; pdb.set_trace()
            raise Exception(NO_AVAILABLE_PORT.format(self=self))

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._close_session()

    def _close_session(self):
        self.stdin, self.stdout = sys.stdin, sys.stdout = self._prev_handles
        if self.active:
            if self._handle is not None:
                self._handle.close()
            if self._client is not None:
                self._client.close()
            if self._sock is not None:
                self._sock.close()
            self.active = False
            self.say(SESSION_ENDED.format(self=self))

    def do_continue(self, arg):
        self._close_session()
        self.set_continue()
        return 1
    do_c = do_cont = do_continue

    def do_quit(self, arg):
        self._close_session()
        self.set_quit()
        return 1
    do_q = do_exit = do_quit

    def set_quit(self):
        # this raises a BdbQuit exception that we're unable to catch.
        sys.settrace(None)

    def cmdloop(self):
        self.do_list(tuple())
        return cmd.Cmd.cmdloop(self)

    def do_list(self, args):
        lines = self.context_lines
        context = (lines - 2) / 2
        if not args:
            first = max(1, self.curframe.f_lineno - context)
            last = first + context * 2 - 1
            args = "(%s, %s)" % (first, last)
        self.lineno = None
        with style(self, (
            self.curframe.f_code.co_filename, self.curframe.f_lineno - context)
        ):
            return Pdb.do_list(self, args)
    do_l = do_list

    def format_stack_entry(self, *args, **kwargs):
        entry = Pdb.format_stack_entry(self, *args, **kwargs)
        return '\n'.join(
            filter(lambda x: not x.startswith('->'), entry.splitlines())
        )

    def print_stack_entry(self, *args, **kwargs):
        with style(self):
            return Pdb.print_stack_entry(self, *args, **kwargs)

    def default(self, line):
        with style(self):
            return Pdb.default(self, line)

    def parseline(self, line):
        line = line.strip()
        match = re.search('^([0-9]+)([a-zA-Z]+)', line)
        if match:
            times, command = match.group(1), match.group(2)
            line = command
            self.cmdqueue.extend(list(command * (int(times) - 1)))
        if line == '?':
            line = 'dir()'
        elif line.endswith('??'):
            line = "import inspect; print ''.join(inspect.getsourcelines(%s)[0][:25])" % line[:-2]
        elif line.endswith('?'):
            line = 'dir(%s)' % line[:-1]
        return cmd.Cmd.parseline(self, line)

    def displayhook(self, obj):
        if obj is not None and not isinstance(obj, list):
            return pprint.pprint(obj)
        return Pdb.displayhook(self, obj)

    def say(self, m):
        logging.warning(m)


def debugger():
    """Return the current debugger instance, or create if none."""
    sdb = _current[0]
    if sdb is None or not sdb.active:
        sdb = _current[0] = Sdb()
    return sdb


def set_trace(frame=None):
    """Set break-point at current location, or a specified frame."""
    if frame is None:
        frame = _frame().f_back
    return debugger().set_trace(frame)


@contextlib.contextmanager
def style(im_self, filepart=None, lexer=None):

    lexer = PythonLexer
    old_stdout = im_self.stdout
    buff = StringIO()
    im_self.stdout = buff
    yield

    value = buff.getvalue()
    context = len(value.splitlines())
    file_cache = {}

    if filepart:
        filepath, lineno = filepart
        if filepath not in file_cache:
            with open(filepath, 'r') as source:
                file_cache[filepath] = source.readlines()
        value = ''.join(file_cache[filepath][:lineno - 1]) + value

    formatter = Terminal256Formatter(style='friendly')
    value = highlight(value, lexer(), formatter)

    # Properly format line numbers when they show up in multi-line strings
    strcolor, _ = formatter.style_string['Token.Literal.String']
    intcolor, _ = formatter.style_string['Token.Literal.Number.Integer']
    value = re.sub(
        r'%s([0-9]+)' % re.escape(strcolor),
        lambda match: intcolor + match.group(1) + strcolor,
        value,
    )

    # Highlight the "current" line in yellow for visibility
    lineno = im_self.curframe.f_lineno

    value = re.sub(
        '(?<!\()%s%s[^\>]+>[^\[]+\[39m([^\x1b]+)[^m]+m([^\n]+)' % (re.escape(intcolor), lineno),
        lambda match: ''.join([
            str(lineno),
            ' ->',
            '\x1b[93m',
            match.group(1),
            re.sub('\x1b[^m]+m', '', match.group(2)),
            '\x1b[0m'
        ]),
        value
    )

    if filepart:
        _, first = filepart
        value = '\n'.join(value.splitlines()[-context:]) + '\n'

    if value.strip():
        old_stdout.write(value)
    im_self.stdout = old_stdout


def listen():
    queue = Queue()

    def _consume(queue):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('0.0.0.0', 6899))
        print('listening for sdb notifications on :6899...')
        while True:
            r, w, x = select.select([sock], [], [])
            for i in r:
                data = i.recv(1024)
                queue.put(data)
    worker = threading.Thread(target=_consume, args=(queue,))
    worker.setDaemon(True)
    worker.start()

    try:
        while True:
            try:
                port = queue.get(timeout=1)
                queue.task_done()
                if port == 'q':
                    break
                port = int(port)
                print('opening telnet session at port :%d...' % port)
                telnet(port)
                print('listening for sdb notifications on :6899...')
            except Empty:
                pass
    except KeyboardInterrupt:
        print('got Ctrl-C')
        queue.put('q')


def telnet(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)

    try:
        s.connect(('0.0.0.0', port))
    except Exception:
        print('unable to connect')
        return
    print('connected to %s:%d' % ('0.0.0.0', port))

    while True:
        socket_list = [sys.stdin, s]
        try:
            r, w, e = select.select(socket_list, [], [])
            for sock in r:
                if sock == s:
                    data = sock.recv(4096)
                    if not data:
                        print('connection closed')
                        return
                    else:
                        sys.stdout.write(data)
                else:
                    msg = sys.stdin.readline()
                    s.send(msg)
        except select.error as e:
            if e[0] != errno.EINTR:
                raise


if __name__ == '__main__':
    listen()
