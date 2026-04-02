Webleank
========

This repository is online at both:

* [gitlab.com](https://gitlab.com/castedo/webleank) for active development, and
* [github.com](https://github.com/castedo/webleank) as a mirror.


Webleank implements WebSocket and web application functionality on top of the core
functionality of [lspleanklib](https://gitlab.com/castedo/lspleanklib).


For the user guide and background information,
visit [lean.castedo.com/webleank](https://lean.castedo.com/webleank).


Configuration reference
-----------------------

Webleank stores and reads its configuration from a file named `webleank.toml`, located in a `lean`
subdirectory within the user configuration directory.
For the exact file location, see the [Platform directories
reference](#platform-directories-reference) section.

The default `webleank.toml` configuration file is:
```
[allowed]
domains = ['127.0.0.1', 'localhost', 'castedo.com']
```

The first time `webleank service` runs, it checks whether the `lean` configuration
subdirectory exists. If it does not, it will write this default `webleank.toml`
file.

The `allowed.domains` setting specifies under which domains web-based *Lean editor
sidekick* applications are allowed to connect to the `localhost:1342` WebSocket.

For more information on the TOML format, visit [toml.io](https://toml.io).


CLI reference
-------------

The `webleank` program provides functionality for web-based Lean editor
sidekick applications. For reference information on the low-level program `lspleank`,
see [the lspleanklib README](https://gitlab.com/castedo/lspleanklib).

```
$ webleank -h
usage: webleank [-h] [--version] {connect,start,service} ...

Link Lean sidekick web apps to LSP-enabled editors

positional arguments:
  {connect,start,service}
    connect             run as stdio LSP server connecting to lspleank socket
    start               start webleank service as detached background process
    service             run as webleank service process

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
```

### Subcommand `connect`

The command `webleank connect` is used in the same situations where `lake serve` would be used.
It runs an LSP server as a stdio subprocess of an editor.
However, instead of running `lake serve` as a subprocess, `webleank connect` connects
to a *lspleank socket* to indirectly connect to `lake serve` running in a
separate parallel process.

Running
```
webleank connect
```
is functionally equivalent to running
```
lspleank connect -- webleank start
```

### Subcommand `start`

The command `webleank start` is used to ensure a `webleank` background service is
running and acting as a server on the *lspleank user socket*.
The command will exit as soon as it determines a server is available on the
*lspleank user socket*. It will start `webleank service` as a detached background
process if one is not running.


### Subcommand `service`

> [!tip]
> Running `webleank service` from the command line is very useful for debugging because
> error information will appear on stdout and/or stderr.

The command `webleank service` will run Webleank as a service, listening on the *lspleank user
socket* and `localhost:1342` as the Webleank control panel and sidekick WebSocket.

Running this command alone will not cause Webleank to run in the background. Running
`webleank start` will cause this process to run in the background as a detached process.
By default, `webleank service` will terminate after a number of idle seconds if nothing connects
to one of the sockets it is servicing.


Platform directories reference
------------------------------

Webleank creates a `lean` subdirectory in:

* the user configuration directory, and
* the user runtime directory.

Webleank uses the [platformdirs Python package](https://platformdirs.readthedocs.io) to
determine their platform-specific file system locations.
On Linux, these correspond to `XDG_CONFIG_HOME` (default `~/.config`) and
`XDG_RUNTIME_DIR`, respectively.
On macOS, they correspond to `~/Library/Application Support/` and
`~/Library/Caches/TemporaryItems/`, respectively.
For other operating systems, consult the [platformdirs
documentation](https://platformdirs.readthedocs.io/en/latest/platforms.html).
