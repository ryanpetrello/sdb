from unittest import TestCase

import six

from sdb import telnet


class TestTelnet(TestCase):

    def setUp(self):
        self.stdin = six.StringIO()
        self.stdout = six.StringIO()

        class t(telnet):
            sent = six.StringIO()

            def _send(self, line):
                self.sent.write(line.decode('utf-8'))

        self.t = t(6899, self.stdin, self.stdout)

    def char(self, char):
        pos = self.stdin.tell()
        self.stdin.write(char)
        self.stdin.seek(pos)
        self.t.send()

    def test_simple_command(self):
        for x in 'list':
            self.char(x)
        self.char('\n')
        assert self.t.sent.getvalue() == 'list\n'
        self.t.recv('<list output>'.encode('utf-8'))
        assert self.stdout.getvalue() == 'list\n<list output>'
        assert self.t.history == ['list']

    def test_history(self):
        for word in ('list', 'next', 'continue'):
            for x in word:
                self.char(x)
            self.char('\n')
        assert self.t.history == ['list', 'next', 'continue']
        assert self.t.line_buff == ''
        self.char('\x1b[A')
        assert self.t.line_buff == 'continue'
        self.char('\x1b[A')
        assert self.t.line_buff == 'next'
        self.char('\x1b[A')
        assert self.t.line_buff == 'list'
        self.char('\x1b[A')
        self.char('\x1b[A')
        self.char('\x1b[A')
        self.char('\x1b[A')
        assert self.t.line_buff == ''
        self.char('\x1b[B')
        assert self.t.line_buff == 'list'
        self.char('\x1b[B')
        assert self.t.line_buff == 'next'
        self.char('\x1b[B')
        assert self.t.line_buff == 'continue'
        self.char('\x1b[B')
        assert self.t.line_buff == ''
        self.char('\x1b[B')
        self.char('\x1b[B')
        self.char('\x1b[B')
        self.char('\x1b[B')
        self.char('\x1b[A')
        assert self.t.line_buff == 'continue'

    def test_backspace(self):
        for x in 'list':
            self.char(x)
        self.char('\x7f')
        self.char('\x7f')
        self.char('\x7f')
        self.char('\n')
        assert self.t.sent.getvalue() == 'l\n'
        self.t.recv('<list output>'.encode('utf-8'))

    def test_single_tab_complete(self):
        self.char('l')
        self.char('i')
        self.char('\t')
        assert self.t.sent.getvalue() == 'li<!TAB!>\n'
        assert self.t.completing == 'li'
        self.t.recv('list'.encode('utf-8'))
        assert self.t.line_buff == 'list'
        assert self.stdout.getvalue() == 'li\x1b[2K\rlist'

    def test_multi_tab_complete(self):
        self.char('l')
        self.char('i')
        self.char('\t')
        assert self.t.sent.getvalue() == 'li<!TAB!>\n'
        assert self.t.completing == 'li'
        self.t.recv('list lit live'.encode('utf-8'))
        assert self.t.line_buff == 'list'
