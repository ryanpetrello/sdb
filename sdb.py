from __future__ import print_function

import cmd
import contextlib
import errno
import logging
import os
import pprint
import re
import rlcompleter
import select
import signal
import socket
import sys
import termios
import threading
import traceback
import tty
from multiprocessing import process
from pdb import Pdb
import six
from six.moves.queue import Queue, Empty
from pygments import highlight
from pygments.lexers import PythonLexer
from pygments.formatters import Terminal256Formatter


__all__ = (
    'SDB_HOST', 'SDB_PORT', 'SDB_NOTIFY_HOST', 'SDB_COLORIZE',
    'DEFAULT_PORT', 'Sdb', 'debugger', 'set_trace',
)

DEFAULT_PORT = 6899

SDB_HOST = os.environ.get('SDB_HOST') or '127.0.0.1'
SDB_PORT = int(os.environ.get('SDB_PORT') or DEFAULT_PORT)
SDB_NOTIFY_HOST = os.environ.get('SDB_NOTIFY_HOST') or '127.0.0.1'
SDB_CONTEXT_LINES = os.environ.get('SDB_CONTEXT_LINES') or 60
SDB_COLORIZE = bool(int(os.environ.get('SDB_COLORIZE') or 1))

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
                 port_search_limit=100, port_skew=+0, out=sys.stdout,
                 colorize=SDB_COLORIZE, interactive=False):
        self.active = True
        self.out = out
        self.colorize = colorize

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

        self.interactive = interactive
        if self.interactive is False:
            self.say(BANNER.format(self=self))
            self._client, address = self._sock.accept()
            self._client.setblocking(1)
            self.remote_addr = ':'.join(str(v) for v in address)
            self.say(SESSION_STARTED.format(self=self))

            self._handle = sys.stdin = sys.stdout = self._client.makefile('rw')
            Pdb.__init__(self, stdin=self._handle, stdout=self._handle)
        else:
            Pdb.__init__(self, stdin=sys.stdin, stdout=sys.stdout)
        self.prompt = ''

    def complete(self, text):
        ns = {}
        ns.update(self.curframe.f_globals.copy())
        ns.update(self.curframe.f_locals.copy())
        ns.update(__builtins__)
        self._completer.namespace = ns
        self._completer.use_main_ns = 0
        self._completer.complete(text, 0)
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
                        str(this_port).encode('utf-8'),
                        (self.notify_host, 6899)
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
        if not self.interactive and self.active:
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
            last = first + context * 2
            args = six.text_type('%s, %s') % (
                six.text_type(int(first)),
                six.text_type(int(last)),
            )
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
        match = re.search('^([0-9]+)([a-zA-Z]+.*)', line)
        if match:
            times, command = match.group(1), match.group(2)
            line = command
            self.cmdqueue.extend([
                command for _ in range(int(times) - 1)
            ])
        if line.startswith('lines '):
            try:
                self.context_lines = int(line.split(' ')[1])
                line = 'l'
            except ValueError:
                pass
        if line == '?':
            line = 'dir()'
        elif line.endswith('??'):
            line = "import inspect; print(''.join(inspect.getsourcelines(%s)[0][:25]))" % line[:-2]  # noqa
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

    def _runmodule(self, module_name):
        self._wait_for_mainpyfile = True
        self._user_requested_quit = False
        import runpy
        mod_name, mod_spec, code = runpy._get_module_details(module_name)
        self.mainpyfile = self.canonic(code.co_filename)
        import __main__
        __main__.__dict__.update({
            "__name__": "__main__",
            "__file__": self.mainpyfile,
            "__package__": mod_spec.parent,
            "__loader__": mod_spec.loader,
            "__spec__": mod_spec,
            "__builtins__": __builtins__,
        })
        self.run(code)

    def _runscript(self, filename):
        self._wait_for_mainpyfile = True
        self.mainpyfile = self.canonic(filename)
        self._user_requested_quit = False
        with open(filename, "rb") as fp:
            statement = "exec(compile(%r, %r, 'exec'))" % \
                        (fp.read(), self.mainpyfile)

        import pdb
        l = locals()
        l['pdb'] = pdb
        self.run(statement, locals=l)


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


def sigtrap(*args, **kw):
    signal.signal(
        signal.SIGTRAP,
        lambda signum, frame: Sdb(*args, **kw).set_trace(frame.f_back)
    )


@contextlib.contextmanager
def style(im_self, filepart=None, lexer=None):

    lexer = PythonLexer
    old_stdout = im_self.stdout

    class NoneBuffer(six.StringIO):
        def write(self, x):
            if x == '':
                x = "''"
            six.StringIO.write(self, x)
    buff = NoneBuffer()
    im_self.stdout = buff
    yield

    value = buff.getvalue()
    context = len(value.splitlines())
    file_cache = {}

    if filepart:
        try:
            filepath, lineno = filepart
            if filepath not in file_cache:
                with open(filepath, 'r') as source:
                    file_cache[filepath] = source.readlines()
            value = ''.join(file_cache[filepath][:int(lineno) - 1]) + value
        except:
            pass

    if not value.strip():
        value = 'None\n'

    if im_self.colorize is True:
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
            '(?<!\()%s%s[^\>]+>[^\[]+\[39m([^\x1b]+)[^m]+m([^\n]+)' % (re.escape(intcolor), lineno),  # noqa
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
                telnet(port).connect()
                print('listening for sdb notifications on :6899...')
            except Empty:
                pass
    except KeyboardInterrupt:
        print('got Ctrl-C')
        queue.put('q')
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, orig_tty)


class telnet(object):

    line_buff = ''
    completing = None
    history_pos = 0

    def __init__(self, port, stdin=sys.stdin, stdout=sys.stdout):
        self.port = port
        self.stdin = stdin
        self.stdout = stdout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.history = []

    def connect(self):
        try:
            self.sock.connect(('0.0.0.0', self.port))
        except Exception:
            print('unable to connect')
            return
        print('connected to %s:%d' % ('0.0.0.0', self.port))

        while True:
            socket_list = [self.stdin, self.sock]
            try:
                r, w, e = select.select(socket_list, [], [])
                for sock in r:
                    if sock == self.sock:
                        data = self.sock.recv(4096 * 4)
                        if not data:
                            print('connection closed')
                            return
                        self.recv(data)
                    else:
                        self.send()
            except select.error as e:
                if e[0] != errno.EINTR:
                    raise

    def recv(self, data):
        if self.completing is not None:
            self.stdout.write('\x1b[2K\r>>> ')
            matches = data.decode('utf-8').split(' ')
            first = matches[0]
            if len(matches) > 1:
                if self.completing:
                    self.line_buff = self.line_buff.replace(
                        self.completing, first
                    )
                    matches[0] = (
                        '\033[93m' + first + '\033[0m'
                    )
                    self.stdout.write(
                        '\n'.join(matches) + '\n' + self.line_buff
                    )
            else:
                if self.completing:
                    self.line_buff = self.line_buff.replace(
                        self.completing, first
                    )
                self.stdout.write(self.line_buff)
        else:
            self.stdout.write('\n')
            self.stdout.write(data.decode('utf-8'))
            self.stdout.write('>>> ')
        self.stdout.flush()

    def send(self):
        char = self.stdin.read(1)
        if char == '\x1b':
            char += self.stdin.read(2)
            if char in ('\x1b[A', '\x1b[B'):
                if char == '\x1b[A':
                    # history up
                    self.history_pos -= 1
                if char == '\x1b[B':
                    # history down
                    self.history_pos += 1

                if self.history_pos < 0:
                    self.history_pos = -1
                    self.line_buff = ''
                else:
                    try:
                        self.line_buff = self.history[self.history_pos]
                    except IndexError:
                        self.history_pos = len(self.history)
                        self.line_buff = ''
                self.stdout.write('\x1b[2K\r>>> %s' % self.line_buff)
        elif char == '\n':
            # return char
            self.completing = None
            self.history_pos += 1
            self.history.append(self.line_buff)
            self._send(
                self.line_buff.encode('utf-8') + '\n'.encode('utf-8')
            )
            self.line_buff = ''
        elif char == '\t':
            # tab complete
            self.completing = self.line_buff.rsplit(' ', 1)[-1]
            self._send(
                self.completing.encode('utf-8') + '<!TAB!>\n'.encode('utf-8')  # noqa
            )
        elif char in ('\x08', '\x7f'):
            # backspace, delete
            self.line_buff = self.line_buff[:-1]
            self.stdout.write('\x1b[2K\r>>> %s' % self.line_buff)
        elif char == '\x15':
            # line clear
            self.stdout.write('\x1b[2K\r>>> ')
        else:
            self.line_buff += char
            self.stdout.write(char)
        self.stdout.flush()

    def _send(self, line):
        self.sock.send(line)  # pragma: nocover


def main():
    import getopt
    opts, args = getopt.getopt(sys.argv[1:], 'm', [])

    run_as_module = False
    for opt, optarg in opts:
        if opt in ['-m']:
            run_as_module = True

    if not args:
        print('Error: Please specify a script or module')
        sys.exit(1)

    script = args[0]
    if not run_as_module and not os.path.exists(script):
        print('Error:', script, 'does not exist')
        sys.exit(1)

    sys.argv[:] = args

    debugger = Sdb(interactive=True)
    try:
        if run_as_module:
            debugger._runmodule(script)
        else:
            debugger._runscript(script)
    except SyntaxError:
        traceback.print_exc()
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == '__main__':
    main()  # pragma: nocover
