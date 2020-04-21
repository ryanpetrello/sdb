Sdb
===
A socket-based remote debugger for Python.  Based on `celery.contrib.rdb`.

![example](https://github.com/ryanpetrello/sdb/blob/master/screen.gif?raw=true)

Usage
-----

Use the `sdb` library to set remote breakpoints in any non-interactive or
background Python code and debug interactively over a telnet session:

```python
   # some/python/code.py

   class SomeCode(object):

       def run(self, **kwargs):
           # This will set a breakpoint and open an interactive Python
           # debugger exposed on a random port between 6899-6999.  The chosen
           # port will be reported as a warning
           #
           # Remote Debugger:6900: Please telnet into 0.0.0.0 6900.
           #
           # You can access it from your host machine using telnet:
           #
           # $ telnet <hostname> <port>
           import sdb
           sdb.set_trace()
```

Keep in mind that when you interactively debug in this way, any process
that encounters a breakpoint will wait until an active client is established
and concludes the debugging session with a `continue` command.

Automatically Connecting to Breakpoints
---------------------------------------

To simplify remote debugging session management, you can use `sdb-listen`
to automatically discover open remote debugging sessions and connect to them:

```shell
   $ sdb-listen
```

This will open a Python process that listens for new debugger sessions and
automatically connects to them for you.  If your breakpoint is run on
an _entirely different host_ (e.g. `10.0.0.1`) you can optionally specify the
hostname where `sdb-listen` is running:

```python
   import sdb
   sdb.Sdb(notify_host='10.0.0.1').set_trace()
```

For OSX users, the Docker for Mac application relies on a separate virtual
machine running the containers. A unique hostname is advertised to containers
pointing back to the virtual machine called: `docker.for.mac.host.internal`.

The breakpoint can then be configured to use that host so that it works
seamlessly in a Mac:

```python
   import sdb
   sdb.Sdb(notify_host='docker.for.mac.host.internal').set_trace()
```

The `sdb-listen` tool also includes support for tab-completion and history
tracking.

Configuration with Environment Variables
----------------------------------------

It is possible to set environment variables to configure `sdb`, which allows
to avoid remembering hosts and ports at the time of setting the breakpoints.

- `SDB_HOST` : Defaults to `127.0.0.1` and it is the address that `sdb` should
bind to.
- `SDB_PORT` : Defaults to `6899`, and it is the port used to bind (used with
`SDB_HOST`). Note that `sdb` has a range of ports from `6899` to `6999`.
- `SDB_NOTIFY_HOST` : To advertise a different host, useful for a separate remote
host like in the case of Docker for Mac.
- `SDB_CONTEXT_LINES` : How much context should get printed when a breakpoint is
reached. Defaults to 60.
- `SDB_COLORIZE` : Toggle to enable or disable colorized output. Defaults to
enabled.

Triggering sdb with a Signal
----------------------------
If you want to debug a running process without setting a specific breakpoint,
a `set_trace()` call can be triggered via `SIGTRAP`:

```python
import sdb
sdb.sigtrap()

long_running_process()
```

```shell
$ kill -5 <pid-of-process>
```

This is particularly useful for investigating Python processes that appear to
be hung.

Docker Compose Examples
-----------------------

If using Docker Compose, it is useful to open the range of ports that `sdb`
supports, as well as injecting the environment variables to configure the
connection.

For example:

```yaml
    environment:
    - SDB_NOTIFY_HOST=docker.for.mac.host.internal
    - SDB_HOST=0.0.0.0
    - SDB_PORT=6899
```

It is useful to define a range of ports that `sdb` can use:

```yaml
    ports:
    - "6899-6999:6899-6999"
```

Other Tips
----------
`sdb` supports the same commands and aliases as Python's [default pdb implementation](https://docs.python.org/2/library/pdb.html#debugger-commands).

`sdb` colorizes output by default.  To disable this:


```python
   import sdb
   sdb.Sdb(colorize=False).set_trace()
```

`sdb` includes a few additional debugger aliases that make interactive debugging more pleasant:

- Prefix commands with an integer to repeat them.  For example, `10n` is the same as running `next` 10 times in a row.
- `?` is the same as calling `dir()`
- `??` can be added to the end of a function call to view its source lines e.g., `requests.post??` might print:

```python
def post(url, data=None, json=None, **kwargs):
    r"""Sends a POST request.

    :param url: URL for the new :class:`Request` object.
    :param data: (optional) Dictionary (will be form-encoded), bytes, or file-like object to send in the body of the :class:`Request`.
    :param json: (optional) json data to send in the body of the :class:`Request`.
    :param \*\*kwargs: Optional arguments that ``request`` takes.
    :return: :class:`Response <Response>` object
    :rtype: requests.Response
    """

    return request('post', url, data=data, json=json, **kwargs)
```
- By default, `sdb` attempts to fill your entire console with debugger output (representing the current line position for the current frame).  You can adjust the height of `sdb`'s draw window with the `lines` command, e.g., `lines 15`.
