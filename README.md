---
title: webleank
---

This repository is online at both:

* [gitlab.com](https://gitlab.com/castedo/webleank) for active development, and
* [github.com](https://github.com/castedo/webleank) as a mirror.


Webleank implements WebSocket and web application functionality on top of the core
functionality of [lspleanklib](https://gitlab.com/castedo/lspleanklib).


For more information, visit [lean.castedo.com](https://lean.castedo.com).


CLI Reference
=============

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
