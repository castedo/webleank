#!/usr/bin/env -S just --justfile

default:
    just --list

test:
    ruff check webleank || true
    mypy --strict webleank

clean:
    rm -rf dist
    rm -rf build
    rm -rf *.egg-info
    rm -f _version.py
