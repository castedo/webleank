#!/usr/bin/env -S just --justfile

default:
    just --list

test:
    ruff check webleank || true
    mypy --strict webleank

build:
  cd webapp && npm install && npm run build
  rm -rf webleank/webapp && mv webapp/dist webleank/webapp
  python3 -m build --sdist --wheel --no-isolation

clean:
    rm -rf dist
    rm -rf build
    rm -rf *.egg-info
    rm -f _version.py
    rm -rf webapp/dist

clean-fresh: clean
    rm -rf webapp/node_modules
