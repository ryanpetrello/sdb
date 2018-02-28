import inspect
import multiprocessing
import select
import socket
import time
import unittest

import pexpect
import six

import sdb

HOST = '127.0.0.1'

class TestSocketTrace(unittest.TestCase):

    def setUp(self):
        # call set_trace() in a child process so we can connect to it
        p = multiprocessing.Process(target=self.set_trace)
        p.start()
        # listen for UDP announcement packets
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((HOST, 6899))
        r, w, x = select.select([sock], [], [])
        for i in r:
            self.port = i.recv(1024).decode('utf-8')

    def set_trace(self):
        time.sleep(1)
        sdb.Sdb(notify_host=HOST, colorize=False).set_trace()


class TestBasicConnectivity(TestSocketTrace):

    def test_udp_announcement(self):
        assert 6899 <= int(self.port) < 7000
        child = pexpect.spawn('telnet', [HOST, self.port])
        child.sendline('c')
        child.expect([pexpect.EOF])
        assert not child.isalive()


class TestControlCommands(TestSocketTrace):

    def assert_command_yields(self, command, expected_lines):
        stdout = six.BytesIO() if six.PY3 else six.StringIO()
        child = pexpect.spawn('telnet', [HOST, self.port])
        child.logfile_read = stdout
        child.sendline(command)
        child.sendline('c')
        child.expect([pexpect.EOF])
        assert not child.isalive()
        for line in expected_lines:
            assert line.encode('utf-8') in stdout.getvalue()

    def test_list(self):
        self.assert_command_yields(
            'list',
            [line.strip() for line in inspect.getsourcelines(self.set_trace)[0]]
        )

    def test_bt(self):
        self.assert_command_yields(
            'bt',
            '> ' + __file__
        )

    def test_locals_alias(self):
        self.assert_command_yields(
            '?',
            "['__return__', 'self']"
        )

    def test_completion_alias(self):
        self.assert_command_yields(
            'sdb?',
            "'SDB_HOST',"
        )

    def test_sourcelines_alias(self):
        self.assert_command_yields(
            'sdb.listen??',
            ['def listen():']
        )

    def test_tab_completion(self):
        self.assert_command_yields(
            'sdb.set_tr<!TAB!>',
            'sdb.set_trace()'
        )

    def test_setlines(self):
        stdout = six.BytesIO() if six.PY3 else six.StringIO()
        child = pexpect.spawn('telnet', [HOST, self.port])
        child.logfile_read = stdout

        # signal that we only want 10 lines of output, and then read the buffer
        child.sendline('lines 10')
        child.expect([pexpect.TIMEOUT], timeout=0.1)

        # the next list call should only return 10 lines of output
        stdout = six.BytesIO() if six.PY3 else six.StringIO()
        child.logfile_read = stdout
        child.sendline('l')
        child.sendline('c')
        child.expect([pexpect.EOF])
        assert not child.isalive()

        # there should only be 10 lines of list output and a line for the final
        # `continue` command
        assert len(stdout.getvalue().splitlines()) == 11
