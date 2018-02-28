import inspect
import multiprocessing
import select
import socket
import time
import unittest

import pexpect
from six import StringIO

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
            self.port = i.recv(1024)

    def set_trace(self):
        time.sleep(1)
        sdb.Sdb(notify_host=HOST, colorize=False).set_trace()


class TestBasicConnectivity(TestSocketTrace):

    def test_udp_announcement(self):
        assert 6899 <= int(self.port) < 7000


class TestControlCommands(TestSocketTrace):

    def assert_command_yields(self, command, expected_lines):
        stdout = StringIO()
        child = pexpect.spawn('telnet', [HOST, self.port])
        child.logfile_read = stdout
        child.sendline(command)
        child.sendline('c')
        child.expect([pexpect.EOF])
        assert not child.isalive()
        for line in expected_lines:
            assert line in stdout.getvalue()

    def test_list(self):
        self.assert_command_yields(
            'list',
            [line.strip() for line in inspect.getsourcelines(self.set_trace)[0]]
        )
