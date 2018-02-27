from __future__ import print_function

import cmd
import contextlib
import errno
import logging
import os
import pprint
import re
import readline
import rlcompleter
import select
import socket
import sys
import termios
import threading
import tty
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


class SocketCompleter(rlcompleter.Completer):

    def global_matches(self, text):
        """Compute matches when text is a simple name.
        Return a list of all keywords, built-in functions and names currently
        defined in self.namespace that match.
        """
        matches = []
        n = len(text)
        for word in self.namespace:
            if word[:n] == text and word != "__builtins__":
                matches.append(word)
        return matches


class Sdb(Pdb):
    """Socket-based debugger."""

    me = 'Socket Debugger'
    _prev_outs = None
    _sock = None
    _completer = SocketCompleter()

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
        Pdb.__init__(self, stdin=self._handle, stdout=self._handle)
        self.prompt = ''

    def complete(self, text):
        ns = {}
        ns.update(self.curframe.f_globals.copy())
        ns.update(self.curframe.f_locals.copy())
        ns.update(__builtins__)
        self._completer.namespace = ns
        self._completer.use_main_ns = 0
        matches = self._completer.complete(text, 0)
        return self._completer.matches

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
            Pdb.do_list(self, args)
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
        if line.startswith('lines '):
            try:
                self.context_lines = int(line.split(' ')[1])
                line = 'l'
            except ValueError:
                pass
        if line == '?':
            line = 'dir()'
        elif line.endswith('??'):
            line = "import inspect; print ''.join(inspect.getsourcelines(%s)[0][:25])" % line[:-2]
        elif line.endswith('?'):
            line = 'dir(%s)' % line[:-1]
        return cmd.Cmd.parseline(self, line)

    def emptyline(self):
        pass

    def onecmd(self, line):
        line = line.strip()
        if line.endswith('<!TAB!>'):
            line = line.split('<!TAB!>')[0]
            matches = self.complete(line)
            if len(matches):
                self.stdout.write(' '.join(matches))
                self.stdout.flush()
            return False
        return Pdb.onecmd(self, line)

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

    orig_tty = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
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
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, orig_tty)


def c():
    raise SystemExit()
if 'libedit' in readline.__doc__:
    readline.parse_and_bind("bind ^I rl_complete")
else:
    readline.parse_and_bind("tab: complete")
readline.set_completer(c)


def telnet(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)

    try:
        s.connect(('0.0.0.0', port))
    except Exception:
        print('unable to connect')
        return
    print('connected to %s:%d' % ('0.0.0.0', port))

    line_buff = ''
    completing = None
    matches = []
    history = []
    history_pos = 0
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
                        if completing is not None:
                            sys.stdout.write('\x1b[2K\r')
                            matches = data.split(' ')
                            if len(matches) > 1:
                                if completing:
                                    line_buff = line_buff.replace(completing, matches[0])
                                    matches[0] = '\033[93m' + matches[0] + '\033[0m'
                                    sys.stdout.write('\n'.join(matches) + '\n' + line_buff)
                            else:
                                if completing:
                                    line_buff = line_buff.replace(completing, matches[0])
                                sys.stdout.write(line_buff)
                        else:
                            sys.stdout.write('\n' + data)
                        sys.stdout.flush()
                else:
                    char = sys.stdin.read(1)
                    if char == '\x1b':
                        char += sys.stdin.read(2)
                        if char in ('\x1b[A', '\x1b[B'):
                            if char == '\x1b[A':
                                history_pos -= 1
                            if char == '\x1b[B':
                                history_pos += 1
                            try:
                                line_buff = history[history_pos]
                            except IndexError:
                                line_buff = ''
                                history_pos = 0
                            sys.stdout.write('\x1b[2K\r%s' % line_buff)
                    elif char == '\n':
                        completing = None
                        history_pos = 0
                        history.append(line_buff)
                        s.send(line_buff + '\n')
                        line_buff = ''
                    elif char == '\t':
                        completing = line_buff.rsplit(' ', 1)[-1]
                        s.send(completing + '<!TAB!>\n')
                    elif char in ('\x08', '\x7f'):
                        line_buff = line_buff[:-1]
                        sys.stdout.write('\x1b[2K\r%s' % line_buff)
                    else:
                        line_buff += char
                        sys.stdout.write(char)
                    sys.stdout.flush()
        except select.error as e:
            if e[0] != errno.EINTR:
                raise


if __name__ == '__main__':
    listen()
