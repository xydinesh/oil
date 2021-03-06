<!-- NOTE: This file is at /release/$VERSION/index.html -->

Oil Version 0.4.alpha1
-----------------

### What's New

- The [Release announcement](announcement.html) has a high-level summary of
  changes.
- Details are in the [raw git change log](changelog.html).  Some of these
  changes may not affect the release tarball (e.g. tool changes).

### Docs For End Users

- [INSTALL](doc/INSTALL.html).  How to install OSH.  This text file is also at
  the root of the tarball.
- [OSH Quick Reference](doc/osh-quick-ref.html), with Examples (in progress).
  This document underlies the OSH `help` builtin.  It also gives a rough
  overview of what features OSH implements.

### Docs For Developers

- [Github Wiki for oilshell/oil](https://github.com/oilshell/oil/wiki)

### Tests Results

- [Spec Tests](test/spec.wwz/).  Test OSH behavior against that of existing
  shells.
- [Wild Tests](test/wild.wwz/).  Parsing and translating thousands of shell
  scripts with OSH.
- [Unit Tests](test/unit.wwz/).  Python unit test results.
- [Gold Tests](test/gold.wwz/log.txt).  Comparisons against bash (using
  implicit assertions, no golden output.)
- [osh2oil Tests](test/osh2oil.wwz/log.txt).  Test the conversion of OSH to
  Oil.

### Benchmarks and Metrics

- [OSH Parser Performance](benchmarks.wwz/osh-parser/).  How fast does OSH
  parse compared to other shells?
- [OSH Runtime](benchmarks.wwz/osh-runtime/).  How fast does OSH
  run compared to other shells?
- [Virtual Memory Baseline](benchmarks.wwz/vm-baseline/).  How much memory to
  shells use at startup?
- [OHeap](benchmarks.wwz/oheap/).  Metrics for a possible AST encoding format.
- [Line Counts](metrics/line-counts.wwz/).  How much code is in Oil?


<!-- TODO: 
benchmarks/
  virtual-memory/
src/                       annotated/cross-referenced source code
coverage/                  code coverage in Python and C
metrics/                   line-counts, debug info size?
-->
